import datetime
import os
from hydrogram import Client, filters, enums
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from config import STATIC_2FA_PASSWORD, LOG_CHANNEL, USDT_RATE
from database import (
    get_unique_countries, get_buckets_by_country, 
    buy_item_atomic, get_user, get_product_details, get_order
)
from utils import format_price, get_pagination_keyboard, get_divider

# Helper for Small Caps 
def small_caps(text):
    trans = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ")
    return text.lower().translate(trans)

# ==================================================================
# 📂 STEP 1: COUNTRY SELECTION (The First Layer)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^(cat|page_cat)_(accounts|sessions)"))
async def cat_router(c, cb):
    try:
        await show_category_list(c, cb)
    except Exception as e:
        print(f"Cat Pagination Error: {e}")
        await cb.answer("❌ Error loading page!", show_alert=True)

async def show_category_list(c, message_or_callback):
    is_cb = isinstance(message_or_callback, CallbackQuery)
    msg = message_or_callback.message if is_cb else message_or_callback
    
    # ROOF EXTRACTION
    page = 1
    category = "accounts"
    
    if is_cb:
        data = message_or_callback.data
        if data.startswith("page_cat_"):
            cat_part, page_str = data.replace("page_cat_", "").rsplit("_", 1)
            category = cat_part
            page = int(page_str)
        elif data.startswith("cat_"):
            category = data.replace("cat_", "")

    countries = await get_unique_countries()
    
    if not countries:
        text = "<b>🚫 OUT OF STOCK</b>\n\nNo stock available right now."
        if is_cb:
            return await message_or_callback.answer("Stock Empty!", show_alert=True)
        return await msg.reply_text(text, parse_mode=enums.ParseMode.HTML)

    items_list = []
    for item in countries:
        name = item["_id"] 
        flag = item.get("flag") or "🏳️"
        items_list.append({
            "text": f"{flag} {name}",
            "callback_data": f"country_{category}_{name}"
        })

    kb = get_pagination_keyboard(
        current_page=page, 
        total_count=len(items_list),
        data_list=items_list,
        callback_prefix=f"page_cat_{category}",
        row_width=2 
    )

    header_text = (
        f"<b>🌍 SELECT COUNTRY ({category.upper()})</b>\n"
        f"{get_divider()}\n"
        "👇 <b>Choose a country to see available products:</b>"
    )

    if is_cb:
        await message_or_callback.answer() # STOPS THE LOADING SPINNER
        await msg.edit_text(header_text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)
    else:
        await msg.reply_text(header_text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)



# ==================================================================
# 📂 STEP 2: BUCKET SELECTION (Inside Country)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^(country|page_cty)_(accounts|sessions)_(.+)"))
async def show_country_products(c, cb):
    try:
        data = cb.data
        page = 1
        
        # EXTRACTION FOR COUNTRIES WITH SPACES/UNDERSCORES
        if data.startswith("page_cty_"):
            remainder, page_str = data.replace("page_cty_", "").rsplit("_", 1)
            category, country_name = remainder.split("_", 1)
            page = int(page_str)
        else:
            remainder = data.replace("country_", "")
            category, country_name = remainder.split("_", 1)

        buckets = await get_buckets_by_country(country_name)
        
        if not buckets:
            return await cb.answer(f"⚠️ Stock just finished for {country_name}!", show_alert=True)

        items_list = []
        for b in buckets:
            p_id = b["_id"]
            price = b["price"]
            year = b["year"]
            count = b["count"]
            flag = b.get("flag") or "🏳️"

            btn_text = f"{flag} {year} - ₹{price} [{count}]"
            items_list.append({
                "text": btn_text,
                "callback_data": f"pre_{category}_{p_id}"
            })

        kb = get_pagination_keyboard(
            current_page=page,
            total_count=len(items_list),
            data_list=items_list,
            callback_prefix=f"page_cty_{category}_{country_name}",
            row_width=1
        )
        
        kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back to Countries", callback_data=f"cat_{category}")])

        header_text = (
            f"<b>🚩 {country_name.upper()} - {category.upper()}</b>\n"
            f"{get_divider()}\n"
            f"⚡ <b>Rate:</b> 1 USDT = ₹{USDT_RATE}\n"
            "👇 <b>Select a product bucket below:</b>"
        )
        
        await cb.answer() # STOPS THE LOADING SPINNER
        await cb.message.edit_text(header_text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)
        
    except Exception as e:
        print(f"Product Pagination Error: {e}")
        await cb.answer("❌ Error loading products!", show_alert=True)



# ==================================================================
# 🚥 STEP 3: CONFIRMATION SCREEN
# ==================================================================

@Client.on_callback_query(filters.regex(r"^pre_(accounts|sessions)_([a-zA-Z0-9-]+)"))
async def confirm_purchase_ui(c, cb):
    try:
        data = cb.data.split("_")
        category, product_id = data[1], data[2]
        
        product = await get_product_details(product_id)
        if not product:
            return await cb.answer("⚠️ Item expired or removed!", show_alert=True)

        user = await get_user(cb.from_user.id)
        balance_inr = user.get("balance", 0.0)
        # Handle string balance gracefully
        if isinstance(balance_inr, str): balance_inr = 0.0
        
        price_inr = product["price"]
        country = product["country"]
        year = product.get("year", "Fresh")
        flag = product.get("flag") or "🏳️"

        price_usdt = round(price_inr / USDT_RATE, 3)
        can_buy = balance_inr >= price_inr

        text = (
            f"🛒 <b>CONFIRM PURCHASE</b>\n"
            f"{get_divider()}\n"
            f"📦 <b>Item:</b> {flag} {country} ({year})\n"
            f"💰 <b>Price:</b> ${price_usdt} (₹{price_inr})\n"
            f"💳 <b>Your Wallet:</b> ₹{balance_inr}\n"
            f"{get_divider()}\n"
            "🟢 <b>Safe & Tested Accounts</b>\n"
            "⚠️ <i>No Guarantee after login. Buy at own risk.</i>"
        )

        if can_buy:
            text += "\n\n✅ <i>Sufficient balance available.</i>"
            confirm_btn = InlineKeyboardButton("✅ Pay & Get Item", callback_data=f"exec_{category}_{product_id}")
        else:
            text += f"\n\n❌ <b>Insufficient Funds!</b>\nNeed ₹{round(price_inr - balance_inr, 2)} more."
            confirm_btn = InlineKeyboardButton("➕ Deposit Funds", callback_data="deposit_home")

        buttons = InlineKeyboardMarkup([
            [confirm_btn],
            [InlineKeyboardButton("🔙 Back", callback_data=f"country_{category}_{country}")]
        ])
        
        await cb.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)

    except Exception as e:
        print(f"Confirmation Error: {e}")
        await cb.answer("Error fetching details.", show_alert=True)

# ==================================================================
# ✅ STEP 4: EXECUTION & DELIVERY (Fixed Logic)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^exec_(accounts|sessions)_([a-zA-Z0-9-]+)"))
async def execute_order(c, cb):
    category, product_id = cb.data.split("_")[1], cb.data.split("_")[2]
    user_id = cb.from_user.id
    
    # 1. ATOMIC TRANSACTION
    purchased_item = await buy_item_atomic(user_id, product_id, category)
    
    if not purchased_item:
        return await cb.answer("❌ Transaction Failed! Stock out or Low Balance.", show_alert=True)
    
    # 2. Extract Data
    item_data = purchased_item.get("data", "N/A") # This is the Session String
    phone_number = purchased_item.get("phone", "Unknown") 
    
    order_id = purchased_item["_id"]
    price = purchased_item.get("price", 0)
    country = purchased_item.get("country", "Unknown")
    flag = purchased_item.get("flag", "🏳️")

    # 3. DELIVERY LOGIC SWITCH
    
    # CASE A: ACCOUNTS (Login Assistant - Shows Phone)
    if category == "accounts":
        assistant_text = (
            "<b>📲 LOGIN ASSISTANT</b>\n"
            f"{get_divider()}\n"
            f"📞 <b>Number:</b> <code>{phone_number}</code>\n"
            f"🔐 <b>2FA Password:</b> <code>{STATIC_2FA_PASSWORD}</code>\n\n"
            "<b>📩 Latest Code:</b> <code>Waiting for request...</code>\n\n"
            "ℹ️ <i>Click 'Get Code' AFTER entering the number in Telegram.</i>"
        )
        
        assistant_btns = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Get Code", callback_data=f"otp_{order_id}")],
            [InlineKeyboardButton("📱 Manage Logins", callback_data=f"mng_{order_id}")],
            [InlineKeyboardButton("✅ Done", callback_data="home")]
        ])
        
        await cb.message.edit_text(assistant_text, parse_mode=enums.ParseMode.HTML, reply_markup=assistant_btns)

    # CASE B: SESSIONS (File Delivery)
    else:
        success_text = (
            "<b>✅ ORDER SUCCESSFUL!</b>\n"
            f"{get_divider()}\n"
            f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
            f"🏳️ <b>Country:</b> {flag} {country}\n"
            f"💰 <b>Price:</b> ₹{price}\n"
            f"{get_divider()}\n"
            "👇 <b>SESSION FILE BELOW</b>\n"
            "<i>Download and import into your tool.</i>"
        )
        await cb.message.edit_text(success_text, parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛍 Buy Again", callback_data=f"cat_{category}")]]))
        
        #  Create .session file 
        try:
            filename = f"{phone_number}_{country}.session" if phone_number != "Unknown" else f"Session_{order_id}.session"
            file_path = f"downloads/{filename}"
            
            # Ensure download dir exists
            if not os.path.exists("downloads"):
                os.makedirs("downloads")
            
            # Write string to file
            with open(file_path, "w") as f:
                f.write(item_data)
            
            # Send Document
            await c.send_document(
                chat_id=user_id,
                document=file_path,
                caption=f"📂 <b>Session File</b>\nFormat: Pyrogram String\nOrder: #{order_id}",
                file_name=filename,
                parse_mode=enums.ParseMode.HTML
            )
            
            # Cleanup
            os.remove(file_path)
            
        except Exception as e:
            # Fallback if file generation fails
            await c.send_message(user_id, f"📂 <b>Session String:</b>\n<code>{item_data}</code>", parse_mode=enums.ParseMode.HTML)

    # 4. Public Log
    await send_public_log(c, cb.from_user, country, price, phone_number, flag)

# ==================================================================
# 📢 STEP 5: ADVANCED LOGGING (Ultra Aesthetic & Optimized)
# ==================================================================

async def send_public_log(client, user, country, price, item_data, flag):
    try:
        # Number Masking Logic (+92348•••••)
        raw = str(item_data).replace("+", "")
        if len(raw) > 6:
            masked = f"+{raw[:5]}••••••"
        else:
            masked = "+••••••••"

        
        log_text = (
            "<pre><code> ✅ New Number Purchase Successful </code></pre>\n\n"
            f"<b>━ <u>Country</u>:</b>  <b>{country.title()}</b> {flag}\n"
            f"<b>━ <u>Application</u>:</b>  <i><b>Tеlеgгaм</b></i> 🍷\n\n"
            f"<b>✚ <u>Number</u>:</b>  <code>{masked}</code> 📞\n"
            f"<b>✚ <u>OTP</u>:</b>  <spoiler>******</spoiler> 💬\n"
            f"<b>✚ <u>Server</u>:</b>  <b>(1)</b> 🥂\n"
            f"<b>✚ <u>Password</u>:</b>  <spoiler>{STATIC_2FA_PASSWORD}</spoiler> 🔐\n\n"
            f"<b>✦</b> <i>@ToxicStoreBot</i>  <b>||</b>  <i>@ToxicTgStore</i> <b>✦</b>"
        )
        
        # Hardcoded URL for optimization
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 • Buy Now • 🛒", url="https://t.me/ToxicTGStore")]
        ])
        
        await client.send_message(
            LOG_CHANNEL, 
            log_text, 
            parse_mode=enums.ParseMode.HTML, 
            reply_markup=buttons
        )
    except Exception as e:
        print(f"Logging Error: {e}")
