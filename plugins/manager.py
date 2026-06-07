import asyncio
import re
from hydrogram import Client, filters, enums
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from hydrogram.errors import (
    SessionPasswordNeeded, AuthKeyUnregistered, 
    UserDeactivated, SessionRevoked, UserRestricted,
    MessageNotModified # 🔥 YE ADD KIYA HAI
)

from hydrogram.raw.functions.account import GetAuthorizations, ResetAuthorization
from config import API_ID, API_HASH, STATIC_2FA_PASSWORD
from database import col_orders, col_stock



# ==================================================================
# 🔄 1. GET OTP HANDLER 
# ==================================================================

@Client.on_callback_query(filters.regex(r"^otp_([a-zA-Z0-9-]+)"))
async def get_otp_handler(c, cb):
    order_id = cb.data.split("_")[1]
    
    # 1. Fetch Order
    try:
        from bson import ObjectId
        order = await col_orders.find_one({"_id": ObjectId(order_id)})
        if not order: order = await col_orders.find_one({"_id": order_id})
    except:
        order = await col_orders.find_one({"_id": order_id})
        
    if not order:
        return await cb.answer("❌ Order not found!", show_alert=True)

    # 2. Smart Data Retrieval
    session_string = order.get("data")
    phone_number = order.get("phone", "Unknown")

    # Fallback 1: Check phone field
    if not session_string and phone_number and len(str(phone_number)) > 50:
        session_string = phone_number
        phone_number = "Unknown"

    # Fallback 2: Check Stock
    if not session_string:
        item_id = order.get("item_id")
        if item_id:
            try:
                from bson import ObjectId
                stock_item = await col_stock.find_one({"_id": ObjectId(item_id)})
            except:
                stock_item = await col_stock.find_one({"_id": item_id})
            
            if stock_item:
                session_string = stock_item.get("data") or stock_item.get("phone")

    if not session_string:
        return await cb.answer("❌ Session Missing. Contact Admin.", show_alert=True)

    await cb.answer("🔄 Connecting to Account...", show_alert=False)
    

    temp_client = Client(
        name=":memory:",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        in_memory=True,
        no_updates=True
    )

    try:

        await temp_client.start()
        
        otp_code = None
        

        try:
            # Method A
            async for msg in temp_client.get_chat_history(777000, limit=3):
                if msg.text:
                    match = re.search(r'\b(\d{5})\b', msg.text)
                    if match:
                        otp_code = match.group(1)
                        break
        except Exception:
            # Method B
            try:
                async for dialog in temp_client.get_dialogs(limit=5):
                    if dialog.chat.id == 777000 or "Telegram" in (dialog.chat.first_name or ""):
                        async for msg in temp_client.get_chat_history(dialog.chat.id, limit=1):
                             if msg.text:
                                match = re.search(r'\b(\d{5})\b', msg.text)
                                if match:
                                    otp_code = match.group(1)
                                    break
                    if otp_code: break
            except: pass

        await temp_client.stop()

        # 5. UI Logic
        if otp_code:
            text = (
                f"<b>📲 LOGIN ASSISTANT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 <b>Number:</b> <code>{phone_number}</code>\n"
                f"📩 <b>OTP:</b> <code>{otp_code}</code>\n"
                f"🔐 <b>2FA:</b> <code>{STATIC_2FA_PASSWORD}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Tap code to copy.</i>"
            )
        else:
            text = (
                f"<b>📲 LOGIN ASSISTANT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 <b>Number:</b> <code>{phone_number}</code>\n"
                f"⚠️ <b>Status:</b> No OTP received yet.\n\n"
                "<b>👇 Steps:</b>\n"
                "1. Login on official Telegram App.\n"
                "2. Wait for the code to arrive.\n"
                "3. Click <b>Refresh Again</b> below."
            )

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh Again", callback_data=f"otp_{order_id}")],
            [InlineKeyboardButton("📱 Manage Logins", callback_data=f"mng_{order_id}")],
            [InlineKeyboardButton("✅ Done", callback_data=f"finish_order_{order_id}")]
        ])
        

        try:
            await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            await cb.answer("⏳ No new OTP yet! Please wait a few seconds...", show_alert=True)

    except (AuthKeyUnregistered, SessionRevoked, UserDeactivated):
        await cb.message.edit_text(
            "<b>❌ SESSION DEAD</b>\n"
            "This session is revoked or invalid.\n"
            "Contact admin.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Home", callback_data="home")]])
        )
    except Exception as e:
        #  Extra safety loop
        if "MESSAGE_NOT_MODIFIED" not in str(e):
            await cb.message.edit_text(f"⚠️ Error: {str(e)[:50]}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="home")]]))
        else:
            await cb.answer("⏳ No new OTP yet!", show_alert=True)

    
    finally:
        if temp_client.is_connected:
            await temp_client.stop()


# ==================================================================
# 📱 2. MANAGE LOGINS (List & Terminate)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^mng_([a-zA-Z0-9-]+)"))
async def manage_sessions_handler(c, cb):
    order_id = cb.data.split("_")[1]
    
    # 1. Fetch Order
    try:
        from bson import ObjectId
        oid = ObjectId(order_id)
        order = await col_orders.find_one({"_id": oid})
    except:
        order = await col_orders.find_one({"_id": order_id})

    # 2. SMART DATA RETRIEVAL (Same as OTP)
    session_string = order.get("data")
    phone_val = order.get("phone", "")
    
    if not session_string and phone_val and len(str(phone_val)) > 50:
        session_string = phone_val
        
    if not session_string:
        # Fallback to stock
        item_id = order.get("item_id")
        if item_id:
            try:
                stock = await col_stock.find_one({"_id": ObjectId(item_id)})
            except:
                stock = await col_stock.find_one({"_id": item_id})
            if stock: session_string = stock.get("data") or stock.get("phone")

    if not session_string:
        return await cb.answer("❌ Session Missing!", show_alert=True)

    await cb.message.edit_text("🔄 <b>Fetching Devices...</b>")

    temp_client = Client(
        name=":memory:",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        in_memory=True,
        no_updates=True
    )

    try:
        await temp_client.start()
        
        # Raw Function to get active sessions
        auths = await temp_client.invoke(GetAuthorizations())
        
        buttons = []
        for a in auths.authorizations:
            # Logic: Show device name.
            if a.current:
                buttons.append([InlineKeyboardButton(f"🟢 THIS BOT ({a.app_name})", callback_data="ignore")])
            else:
                # Other devices
                buttons.append([InlineKeyboardButton(f"📱 {a.device_model} | ❌ KILL", callback_data=f"kill_{order_id}_{a.hash}")])
        
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"otp_{order_id}")])
        
        await cb.message.edit_text(
            f"<b>📱 ACTIVE SESSIONS ({len(auths.authorizations)})</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Tap a device to force logout (kill session).</i>",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML
        )
        
        await temp_client.stop()

    except (AuthKeyUnregistered, SessionRevoked, UserDeactivated):
        await cb.message.edit_text(
            "<b>❌ SESSION DEAD</b>\nCannot fetch devices.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="home")]])
        )
    except Exception as e:
        await cb.message.edit_text(f"❌ Error: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="home")]]))
    finally:
        if temp_client.is_connected:
            await temp_client.stop()

# ==================================================================
# 🔴 3. KILL SESSION (Terminate Specific Device)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^kill_([a-zA-Z0-9-]+)_(\d+)"))
async def kill_session_handler(c, cb):
    data = cb.data.split("_")
    order_id = data[1]
    session_hash = int(data[2])
    
    # Fetch Order Again (Need session string to connect)
    try:
        from bson import ObjectId
        oid = ObjectId(order_id)
        order = await col_orders.find_one({"_id": oid})
    except:
        order = await col_orders.find_one({"_id": order_id})
        
    if not order: return await cb.answer("❌ Order missing", show_alert=True)

    # Retrieval Logic
    session_string = order.get("data")
    if not session_string and len(str(order.get("phone", ""))) > 50: 
        session_string = order.get("phone")

    if not session_string: return await cb.answer("❌ Session Missing", show_alert=True)

    await cb.answer("⚡ Terminating Session...", show_alert=False)
    
    temp_client = Client(
        name=":memory:",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        in_memory=True,
        no_updates=True
    )
    
    try:
        await temp_client.start()
        
        # Invoke ResetAuthorization (Kill Session)
        await temp_client.invoke(ResetAuthorization(hash=session_hash))
        
        await cb.answer("✅ Device Logged Out!", show_alert=True)
        
        # Refresh List automatically
        await manage_sessions_handler(c, cb)
        
    except Exception as e:
        await cb.answer(f"❌ Failed: {e}", show_alert=True)
    finally:
        if temp_client.is_connected:
            await temp_client.stop()


# ==================================================================
# ✅ ORDER FINISH / THANK YOU SCREEN
# ==================================================================
@Client.on_callback_query(filters.regex(r"^finish_order_([a-zA-Z0-9-]+)"))
async def finish_order_summary(c, cb):
    order_id = cb.data.split("_")[2]
    
    # Fetch Order for details
    try:
        from bson import ObjectId
        order = await col_orders.find_one({"_id": ObjectId(order_id)})
        if not order: order = await col_orders.find_one({"_id": order_id})
    except:
        order = await col_orders.find_one({"_id": order_id})
        
    if not order:
        return await cb.message.edit_text("✅ <b>Thank You!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]))

    # Extract Details
    item_name = f"{order.get('flag', '🏳️')} {order.get('country', 'Unknown')}"
    price = order.get('price', 0)
    phone = order.get('phone', 'Hidden')
    
    text = (
        "<b>🎉 PURCHASE COMPLETED!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Item:</b> {item_name}\n"
        f"💰 <b>Price:</b> ₹{price}\n"
        f"📞 <b>Phone:</b> <code>{phone}</code>\n"
        f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Thank you for shopping with us!</i>\n"
        "<i>If you face any issues, contact support.</i>"
    )
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 Buy More", callback_data="home")],
        [InlineKeyboardButton("📞 Support", callback_data="help")]
    ])
    
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


# ==================================================================
# 🚫 4. IGNORE DUMMY CALLBACK
# ==================================================================
# 🔥 FIX: Added ^ and $ so it ONLY catches 'ignore_dev'
@Client.on_callback_query(filters.regex("^ignore_dev$"))
async def ignore_callback(c, cb):
    await cb.answer("⚠️ You cannot logout the bot itself!", show_alert=True)

# 🔥 FIX: Separate silent handler for Paginator's "ignore" button
@Client.on_callback_query(filters.regex("^ignore$"))
async def silent_ignore(c, cb):
    await cb.answer()
