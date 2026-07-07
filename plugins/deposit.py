import asyncio
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
def generate_upi_qr(upi_id, amount=None):
    """Generates a UPI QR code image in memory. Includes amount if provided."""
    upi_url = f"upi://pay?pa={upi_id}&pn=Merchant&cu=INR"
    if amount:
        upi_url += f"&am={amount}"
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
# 🇮🇳 UPI FLOW: ASK AMOUNT -> SHOW QR -> WAIT SCREENSHOT
# ==================================================================

@Client.on_callback_query(filters.regex("pay_upi_start"))
async def pay_upi_ask_amount(c, cb):
    """Step 1: Ask user how much they want to deposit."""
    user_id = cb.from_user.id

    # 1. Set State - waiting for amount
    deposit_session[user_id] = {"mode": "waiting_amount", "menu_id": cb.message.id}

    text = (
        "<b>💳 UPI PAYMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>Kitna amount deposit karna chahte hain?</b>\n"
        "<i>Amount number me type karke bhejein (e.g. 500)</i>"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Cancel", callback_data="deposit_home")]
    ])

    try:
        await cb.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)
    except Exception:
        try: await cb.message.delete()
        except: pass
        sent = await c.send_message(user_id, text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)
        deposit_session[user_id]["menu_id"] = sent.id


@Client.on_message(filters.text & filters.private & ~filters.command(["start", "deposit", "admin"]), group=1)
async def check_amount_input(c, msg):
    """Step 2: Read the amount, then show the QR code for that amount."""
    user_id = msg.from_user.id

    # 1. Check State
    if user_id not in deposit_session: return
    state = deposit_session[user_id]
    if state.get("mode") != "waiting_amount": return

    # 2. Cleanup User Input
    try: await msg.delete()
    except: pass

    # 3. Validate Amount
    amount_text = msg.text.strip()
    if not amount_text.isdigit() or int(amount_text) <= 0:
        temp = await c.send_message(user_id, "❌ <b>Galat Amount!</b>\nSahi number bhejein (e.g. 500).")
        await asyncio.sleep(3); await temp.delete()
        return

    amount = int(amount_text)

    # 4. Move to waiting_proof state, store amount + type
    deposit_session[user_id] = {"mode": "waiting_proof", "type": "upi", "amount": amount}

    # 5. Generate Dynamic QR with amount
    qr_image = generate_upi_qr(PAYMENT_UPI_ID, amount)

    text = (
        "<b>💳 UPI PAYMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Amount:</b> ₹{amount}\n"
        f"🆔 <b>UPI ID:</b> <code>{PAYMENT_UPI_ID}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>STEPS TO PAY:</b>\n"
        "1️⃣ Scan QR ya UPI ID copy karein.\n"
        f"2️⃣ Exactly ₹{amount} pay karein.\n"
        "3️⃣ <b>Payment ka screenshot yahan bhejein.</b>\n\n"
        "<i>Bot aapke screenshot ka wait kar raha hai...</i>"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Cancel", callback_data="deposit_home")]
    ])

    sent_msg = await c.send_photo(
        user_id,
        photo=qr_image,
        caption=text,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=buttons
    )
    deposit_session[user_id]["menu_id"] = sent_msg.id


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

    deposit_session[user_id] = {"mode": "waiting_proof", "type": "crypto", "menu_id": cb.message.id}

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

@Client.on_message((filters.photo | filters.document) & filters.private, group=2)
async def handle_payment_proof(c, msg):
    """Handles screenshot proof for BOTH UPI and Crypto deposits."""
    user_id = msg.from_user.id

    # 1. State Check
    if user_id not in deposit_session: return
    state = deposit_session[user_id]
    if state.get("mode") != "waiting_proof": return

    pay_type = state.get("type", "crypto")
    amount = state.get("amount")

    # 2. Build Caption (with amount line if we know it - i.e. UPI flow)
    amount_line = f"💰 <b>Amount:</b> ₹{amount}\n" if amount else ""
    title = "🇮🇳 NEW UPI DEPOSIT" if pay_type == "upi" else "🪙 NEW CRYPTO DEPOSIT"

    caption = (
        f"<b>{title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {msg.from_user.mention} (`{user_id}`)\n"
        f"{amount_line}"
        f"📅 <b>Date:</b> {msg.date}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>Verify & Approve:</b>"
    )

    # Ref tells admin flow what to prefill: amount if known (UPI), else "crypto" (manual entry)
    ref = f"amt{amount}" if amount else "crypto"

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Add Funds", callback_data=f"admin_approve_{user_id}_{ref}")],
        [InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{user_id}")]
    ])

    try:
        file_id = msg.photo.file_id if msg.photo else msg.document.file_id
        await c.send_photo(
            ADMIN_GROUP_ID, 
            photo=file_id, 
            caption=caption, 
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )
        await msg.reply_text("✅ <b>Proof Submitted!</b>\nAdmin approval ka wait karein.", parse_mode=enums.ParseMode.HTML)
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
    ref_id = data[3]  # 'crypto' or 'amt<number>' (user's claimed UPI amount)

    # If ref_id looks like "amt500", extract the claimed amount to show as a hint
    hint = ""
    if ref_id.startswith("amt") and ref_id[3:].isdigit():
        hint = f"💡 <i>User ne screenshot ke hisaab se ₹{ref_id[3:]} pay kiya hai (verify karke confirm karein)</i>\n\n"

    #Amount
    await cb.message.reply_text(
        f"<b>💰 CREDIT AMOUNT (INR)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"User ID: `{user_id}`\n"
        f"Ref: `{ref_id}`\n\n"
        f"{hint}"
        "👇 <i>Screenshot verify karke amount reply karein (e.g. 500):</i>",
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
