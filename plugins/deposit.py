import asyncio
import aiohttp
import qrcode
import io
from hydrogram import Client, filters, enums
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply, CallbackQuery, Message
from config import ADMINS, PAYMENT_UPI_ID, BINANCE_ID, TRC20_ADDRESS, ADMIN_GROUP_ID
from database import get_user, update_balance, create_deposit, get_deposit
from utils import format_price

# ==================================================================
# 🧠 DEPOSIT STATE MANAGEMENT (RAM)
# ==================================================================

deposit_session = {}

def clear_deposit_session(user_id):
    if user_id in deposit_session:
        del deposit_session[user_id]

# ==================================================================
# 🛠️ HELPER: QR CODE GENERATOR (In-Memory)
# ==================================================================
def generate_upi_qr(upi_id):
    """Generates a UPI QR code image in memory."""
    upi_url = f"upi://pay?pa={upi_id}&pn=Merchant&cu=INR"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(upi_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    
    # Save to bytes buffer
    bio = io.BytesIO()
    img.save(bio)
    bio.seek(0)
    return bio

# ==================================================================
# 🏦 DEPOSIT MENU 
# ==================================================================

async def safe_deposit_menu(client, message_or_callback):
    """
    Bulletproof Entry Point.
    Strategy: Define variables FIRST, then try logic. If logic fails, use defaults.
    """

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 UPI (Auto - Fast)", callback_data="pay_upi_start")],
        [InlineKeyboardButton("🪙 Crypto (Manual)", callback_data="pay_crypto")],
        [InlineKeyboardButton("🔙 Back to Home", callback_data="home")]
    ])
    text = "<b>🏦 ADD FUNDS</b>\nLoading Wallet..."
    user_id = message_or_callback.from_user.id
    
    try:
        # 1. Clear Session
        clear_deposit_session(user_id)

        # 2. Determine Context (Message or Callback)
        if isinstance(message_or_callback, CallbackQuery):
            msg = message_or_callback.message
            is_callback = True
        else:
            msg = message_or_callback
            is_callback = False


        try:
            user = await get_user(user_id)
            if not user:
                from database import add_user
                await add_user(user_id, message_or_callback.from_user.first_name)
                user = await get_user(user_id)

            raw_balance = user.get("balance", 0)
            # Handle String/Float/Int safely
            if isinstance(raw_balance, str):
                try: balance_val = float(raw_balance)
                except: balance_val = 0.0
            else:
                balance_val = float(raw_balance)
        except Exception as e:
            print(f"DB Error in Deposit: {e}")
            balance_val = 0.0 # Fallback

        # 4. Final Text Generation
        text = (
            f"<b>🏦 ADD FUNDS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Wallet Balance:</b> {format_price(balance_val)}\n\n"
            "👇 <b>Select Payment Method:</b>"
        )

        # 5. EXECUTION
        if is_callback:
            # Attempt 1:
            try:
                await msg.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)
            except Exception:
              
                try: await msg.delete()
                except: pass 
                
                # Send New Message
                await client.send_message(user_id, text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)
        else:
            # Direct Command (/deposit)
            await msg.reply_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)

    except Exception as e:

        print(f"Critical Deposit Error: {e}")
        try:
            await client.send_message(user_id, text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)
        except: pass


# ✅ ENTRY POINTS
@Client.on_message(filters.command("deposit"))
async def deposit_command(c, msg):
    await safe_deposit_menu(c, msg)

@Client.on_callback_query(filters.regex("deposit_home"))
async def deposit_callback(c, cb):
    await safe_deposit_menu(c, cb)



# ==================================================================
# 🇮🇳 UPI AUTOMATIC FLOW
# ==================================================================

@Client.on_callback_query(filters.regex("pay_upi_start"))
async def pay_upi_show_qr(c, cb):
    user_id = cb.from_user.id
    
    # 1. Set State
    deposit_session[user_id] = {"mode": "waiting_utr", "menu_id": cb.message.id}
    
    # 2. Use Static QR Link
    qr_image_url = "https://i.ibb.co/NdM8BQV6/BHARATPE-QR-1.png"
    
    text = (
        "<b>💳 UPI PAYMENT (Auto-Verify)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>UPI ID:</b> <code>{PAYMENT_UPI_ID}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>STEPS TO PAY:</b>\n"
        "1️⃣ Scan QR or Copy UPI ID.\n"
        "2️⃣ Pay any amount you want.\n"
        "3️⃣ <b>Send the 12-Digit UTR / Ref No. here.</b>\n\n"
        "<i>Bot is listening for UTR...</i>"
    )
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Cancel", callback_data="deposit_home")]
    ])
    
    # Delete old message to send Photo
    try: await cb.message.delete()
    except: pass
    
    sent_msg = await c.send_photo(
        user_id, 
        photo=qr_image_url, 
        caption=text, 
        parse_mode=enums.ParseMode.HTML,
        reply_markup=buttons
    )
    
    # Update session with new message ID
    deposit_session[user_id]["menu_id"] = sent_msg.id

# ==================================================================
# 🕵️‍♂️ UTR LISTENER & API VERIFICATION
# ==================================================================

@Client.on_message(filters.text & filters.private & ~filters.command(["start", "deposit", "admin"]), group=1)
async def check_utr_input(c, msg):
    user_id = msg.from_user.id
    
    # 1. Check State
    if user_id not in deposit_session: return
    state = deposit_session[user_id]
    if state.get("mode") != "waiting_utr": return

    # 2. Cleanup User Input
    try: await msg.delete()
    except: pass

    # 3. Validate Input (12 Digits)
    utr = msg.text.strip()
    if not utr.isdigit() or len(utr) != 12:
        temp = await c.send_message(user_id, "❌ <b>Invalid UTR!</b>\nPlease send a valid 12-digit UTR number.")
        await asyncio.sleep(3); await temp.delete()
        return

    # 4. Check Duplicate in DB
    if await get_deposit(utr):
        temp = await c.send_message(user_id, "⚠️ <b>UTR Already Used!</b>\nContact admin if needed.")
        await asyncio.sleep(3); await temp.delete()
        clear_deposit_session(user_id)
        return

    # 5. Verify 
    status_msg = await c.send_message(user_id, "🔄 <b>Verifying Payment...</b>\n<i>Connecting to banking server...</i>")
    
    try:
        api_url = "https://bharatpe-taupe.vercel.app/api/verify"
        payload = {"utr": utr}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload) as resp:
                data = await resp.json()
        
        # 6. Process API Response
        if data.get("status") == "SUCCESS" and data.get("verified") is True:
            amount = float(data.get("amount_credited", 0))
            payer_name = data.get("payer_name", "Unknown")
            
            # A. Add Balance
            await update_balance(user_id, amount)

            # 
            from database import check_referral_milestone
            referrer_id = await check_referral_milestone(user_id, amount)
            if referrer_id:
                try:
                    await c.send_message(referrer_id, f"🎉 <b>Referral Bonus!</b>\nYour invitee deposited funds.\n💰 <b>You got:</b> ₹20")
                except: pass
            
            # B. Log Transaction
            await create_deposit(user_id, amount, utr, "upi_auto", "success")

            
            # C. Success Message
            success_text = (
                "<b>✅ PAYMENT SUCCESSFUL!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Credited:</b> ₹{amount}\n"
                f"👤 <b>Payer:</b> {payer_name}\n"
                f"🆔 <b>UTR:</b> <code>{utr}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Funds have been added to your wallet.</i>"
            )
            await status_msg.edit_text(
                success_text, 
                parse_mode=enums.ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛍 Buy Now", callback_data="home")]])
            )
            
            # D. Admin Alert
            try:
                await c.send_message(
                    ADMIN_GROUP_ID,
                    f"🤖 <b>Auto-Deposit Alert</b>\n"
                    f"👤 User: {msg.from_user.mention}\n"
                    f"💰 Amount: ₹{amount}\n"
                    f"🆔 UTR: {utr}\n"
                    f"🏦 Name: {payer_name}"
                )
            except: pass
            
            clear_deposit_session(user_id)
            
        else:
            # Failed
            message = data.get("message", "Payment not found or pending.")
            fail_text = (
                f"❌ <b>Verification Failed!</b>\n"
                f"Reason: {message}\n\n"
                "👇 <b>If you paid, request manual review:</b>"
            )
            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("👨‍💻 Request Manual Check", callback_data=f"manual_review_{utr}")],
                [InlineKeyboardButton("🔄 Try Again", callback_data="pay_upi_start")]
            ])
            await status_msg.edit_text(fail_text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
            

    except Exception as e:
        print(f"API Error: {e}")
        await status_msg.edit_text("❌ <b>Server Error!</b>\nPlease try again later or contact admin.")
        clear_deposit_session(user_id)

# ==================================================================
# 👨‍💻 MANUAL REVIEW REQUEST
# ==================================================================

@Client.on_callback_query(filters.regex(r"manual_review_(\d+)"))
async def manual_review_request(c, cb):
    utr = cb.data.split("_")[2]
    user_id = cb.from_user.id
    
    text = (
        "<b>⚠️ MANUAL REVIEW REQUEST</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {cb.from_user.mention} (`{user_id}`)\n"
        f"🆔 <b>UTR:</b> <code>{utr}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>Admin Action:</b>"
    )
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{user_id}_{utr}")],
        [InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{user_id}")]
    ])
    
    try:
        await c.send_message(ADMIN_GROUP_ID, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
        await cb.message.edit_text("✅ <b>Request Sent!</b>\nAdmin will check and update balance shortly.")
        clear_deposit_session(user_id)
    except Exception as e:
        await cb.answer("Error sending request.", show_alert=True)


# ==================================================================
# 🪙 CRYPTO MANUAL FLOW
# ==================================================================

@Client.on_callback_query(filters.regex("pay_crypto"))
async def pay_crypto(c, cb):
    # 1. Safety Defaults
    text = "Loading Crypto Details..."
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="deposit_home")]])
    
    try:
        clear_deposit_session(cb.from_user.id)
        
        text = (
            "<b>🪙 CRYPTO DEPOSIT (USDT)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<b>🆔 Binance Pay ID:</b>\n"
            f"<code>{BINANCE_ID}</code>\n\n"
            "<b>🔗 USDT TRC20 Address:</b>\n"
            f"<code>{TRC20_ADDRESS}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ <b>Min Deposit:</b> $1\n"
            "👇 <b>After payment, upload screenshot below.</b>"
        )
        
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Upload Screenshot", callback_data="submit_crypto_proof")],
            [InlineKeyboardButton("🔙 Back", callback_data="deposit_home")]
        ])
        
        # 2. Smart Send 
        try:
            await cb.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)
        except:
            # Edit failed 
            await cb.message.delete()
            await c.send_message(cb.from_user.id, text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)

    except Exception as e:
        print(f"Crypto Menu Error: {e}")
        # Fail safe
        try: await c.send_message(cb.from_user.id, text, reply_markup=buttons)
        except: pass


@Client.on_callback_query(filters.regex("submit_crypto_proof"))
async def ask_proof(c, cb):
    user_id = cb.from_user.id
    

    deposit_session[user_id] = {"mode": "waiting_proof", "menu_id": cb.message.id}
    
    await cb.message.delete()
    sent = await c.send_message(
        user_id, 
        "<b>📸 SUBMIT PROOF</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Send the payment screenshot now.\n"
        "<i>Caption me Hash ID likhein (Optional).</i>",
        reply_markup=ForceReply(placeholder="Send Image..."),
        parse_mode=enums.ParseMode.HTML
    )
    deposit_session[user_id]["menu_id"] = sent.id

@Client.on_message(filters.reply & (filters.photo | filters.document), group=2)
async def handle_crypto_proof(c, msg):
    user_id = msg.from_user.id
    
    # 1. State Check
    if user_id not in deposit_session: return
    state = deposit_session[user_id]
    if state.get("mode") != "waiting_proof": return

    # 2. Process Proof
    caption = (
        f"<b>🪙 NEW CRYPTO DEPOSIT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {msg.from_user.mention} (`{user_id}`)\n"
        f"📅 <b>Date:</b> {msg.date}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>Verify & Approve:</b>"
    )
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Add Funds", callback_data=f"admin_approve_{user_id}_crypto")],
        [InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{user_id}")]
    ])
    
    try:
        await c.send_photo(
            ADMIN_GROUP_ID, 
            photo=msg.photo.file_id, 
            caption=caption, 
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )
        await msg.reply_text("✅ <b>Proof Submitted!</b>\nWait for admin approval.", parse_mode=enums.ParseMode.HTML)
        clear_deposit_session(user_id)
        
    except Exception as e:
        await msg.reply_text(f"❌ Error: {e}")

# ==================================================================
# 👮 ADMIN APPROVAL LOGIC 
# ==================================================================

@Client.on_callback_query(filters.regex(r"admin_approve_(\d+)_(.+)"))
async def admin_approve_ask(c, cb):
    data = cb.data.split("_")
    user_id = data[2]
    ref_id = data[3] #'crypto'
    
    #Amount
    await cb.message.reply_text(
        f"<b>💰 CREDIT AMOUNT (INR)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"User ID: `{user_id}`\n"
        f"Ref: `{ref_id}`\n\n"
        "👇 <i>Reply with amount (e.g. 500):</i>",
        reply_markup=ForceReply(selective=True),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.reply & filters.regex(r"^\d+$") & filters.chat(ADMIN_GROUP_ID))
async def admin_finalize_deposit(c, msg):
    # Check context
    if msg.reply_to_message and "CREDIT AMOUNT" in msg.reply_to_message.text:
        try:

            target_user_id = int(msg.reply_to_message.text.split("User ID: `")[1].split("`")[0])
            amount = int(msg.text)
            
            # Add Balance
            await update_balance(target_user_id, amount)

            
            from database import check_referral_milestone
            referrer_id = await check_referral_milestone(target_user_id, amount)
            if referrer_id:
                try:
                    await c.send_message(referrer_id, f"🎉 <b>Referral Bonus!</b>\nYour invitee deposited funds.\n💰 <b>You got:</b> ₹20")
                except: pass
            
            # Log
            await create_deposit(target_user_id, amount, "admin_manual", "manual", "success")

            
            # Notify Admin
            await msg.reply_text(f"✅ <b>Done!</b> Added ₹{amount} to `{target_user_id}`.")
            
            # Notify User
            try:
                await c.send_message(
                    target_user_id,
                    f"<b>✅ DEPOSIT APPROVED!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 <b>Credited:</b> ₹{amount}\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "<i>Use /start to check balance.</i>",
                    parse_mode=enums.ParseMode.HTML
                )
            except: pass
            
        except Exception as e:
            await msg.reply_text(f"❌ Error: {e}")

@Client.on_callback_query(filters.regex(r"admin_reject_(\d+)"))
async def admin_reject(c, cb):
    user_id = cb.data.split("_")[2]
    
    try:
        await c.send_message(user_id, "❌ <b>Deposit Rejected.</b>\nReason: Invalid proof or payment not found.")
    except: pass
    
    await cb.message.edit_caption(cb.message.caption + "\n\n🚫 <b>REJECTED BY ADMIN</b>")
