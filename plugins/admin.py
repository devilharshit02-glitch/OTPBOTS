import os
import asyncio
import pycountry
from hydrogram import Client, filters, enums
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from config import ADMINS, LOG_CHANNEL
from database import (
    db, add_stock, get_unique_buckets, get_user, update_balance, 
    col_users, col_stock, col_orders, col_payments, col_settings,
    get_unique_countries, get_buckets_by_country, update_fsub, get_fsub_list, del_fsub,
    update_usdt_rate, set_maintenance, get_maintenance
)

# ==================================================================
# 🧠 ADMIN STATE MANAGEMENT (RAM)
# ==================================================================

admin_session = {}

def clear_session(user_id):
    """Safely clears any active admin listener."""
    if user_id in admin_session:
        del admin_session[user_id]

# ==================================================================
# 🛠️ HELPER: SAFE SEND
# ==================================================================
async def safe_show_dashboard(client, message_or_callback):
    """
    Shows Main Admin Dashboard. Robust fix for Back Button Crash.
    """
    user_id = message_or_callback.from_user.id
    clear_session(user_id) # Stop

    # Fetch Real-time Stats
    total_users = await col_users.count_documents({})
    total_stock = await col_stock.count_documents({"status": "fresh"})

    
    pending_crypto = await col_payments.count_documents({"status": "pending"})
    total_sales = await col_orders.count_documents({}) 

    text = (
        "<b>👮‍♂️ ULTIMATE ADMIN DASHBOARD</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Total Users:</b> {total_users}\n"
        f"📦 <b>Active Stock:</b> {total_stock}\n"
        f"💰 <b>Total Sales:</b> {total_sales}\n"
        f"⏳ <b>Pending Payments:</b> {pending_crypto}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Welcome back, Admin. Select an action:</i>"
    )

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 Stock Manager", callback_data="admin_stock"),
            InlineKeyboardButton(f"💰 Payments ({pending_crypto})", callback_data="admin_payments")
        ],
      
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
            InlineKeyboardButton("👤 User Manager", callback_data="admin_users")
        ],
        [
            InlineKeyboardButton("⚙️ Settings & FSub", callback_data="admin_settings"),
            InlineKeyboardButton("❌ Close", callback_data="close_admin")
        ]
    ])

    # logic
    try:
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
        else:
            await message_or_callback.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
    except Exception:
        try:
            if isinstance(message_or_callback, CallbackQuery):
                await message_or_callback.message.delete()
            await client.send_message(user_id, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
        except: pass


# ==================================================================
# 🏠 ENTRY POINTS
# ==================================================================

@Client.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_panel(client, message):
    await safe_show_dashboard(client, message)

@Client.on_callback_query(filters.regex("admin_home"))
async def home_callback(c, cb):
    await safe_show_dashboard(c, cb)

# ==================================================================
# 📦 STOCK MANAGER (With 2-Step View)
# ==================================================================

@Client.on_callback_query(filters.regex("admin_stock"))
async def stock_menu(c, cb):
    clear_session(cb.from_user.id)
    text = (
        "<b>📦 STOCK MANAGEMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📂 Present Stock:</b> View and add to existing countries.\n"
        "<b>🆕 New Bucket:</b> Create a new country/price category.\n"
        "<b>🗑 Clear Stock:</b> Delete items by country."
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Present Stock (View All)", callback_data="goto_present_admin")],
        [InlineKeyboardButton("🆕 New Bucket (Quick Create)", callback_data="goto_new_admin")],
        [InlineKeyboardButton("🗑 Clear Stock (Bulk)", callback_data="admin_delete_menu")],
        [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_home")]
    ])
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

# Step 1: Admin Country List
@Client.on_callback_query(filters.regex("goto_present_admin"))
async def show_present_countries(c, cb):
    countries = await get_unique_countries()
    if not countries:
        return await cb.answer("⚠️ No stock found in DB!", show_alert=True)
    
    buttons = []
    for item in countries:
        name = item["_id"]
        flag = item.get("flag", "🏳️")
        buttons.append([InlineKeyboardButton(f"{flag} {name}", callback_data=f"adm_cty_{name}")])
    
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_stock")])
    await cb.message.edit_text("<b>🌍 Select Country to Manage:</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

# Step 2: Show Buckets in that country for Admin
@Client.on_callback_query(filters.regex(r"adm_cty_(.+)"))
async def show_country_buckets_admin(c, cb):
    country_name = cb.data.split("_")[2]
    buckets = await get_buckets_by_country(country_name)
    
    buttons = []
    for b in buckets:
        # Construct callback data
        btn_text = f"{b.get('flag','🏳️')} {b['year']} - ₹{b['price']} [{b['count']}]"
        cb_data = f"pre_upload_{country_name}_{b['price']}_{b['year']}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=cb_data)])
    
    buttons.append([InlineKeyboardButton("🔙 Back to Countries", callback_data="goto_present_admin")])
    await cb.message.edit_text(f"<b>🚩 Managing: {country_name}</b>\nClick a bucket to add more stock:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

# Create New Bucket Trigger
@Client.on_callback_query(filters.regex("goto_new_admin"))
async def new_bucket_ask(c, cb):
    admin_session[cb.from_user.id] = {"mode": "smart_input", "menu_id": cb.message.id}
    await cb.message.edit_text(
        "<b>🆕 Smart Bucket Creator</b>\n\n"
        "Send: <code>Country Price Year</code>\n"
        "Example: <code>USA 100 2024</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_stock")]]),
        parse_mode=enums.ParseMode.HTML
    )

# ==================================================================
# 👤 USER MANAGER (Ban, Add Money, Search)
# ==================================================================

@Client.on_callback_query(filters.regex("admin_users"))
async def user_manager_menu(c, cb):
    clear_session(cb.from_user.id)
    text = (
        "<b>👤 USER MANAGER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Search a user by ID to modify balance or ban/unban them."
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Search User by ID", callback_data="search_user_input")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
    ])
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex("search_user_input"))
async def search_input_trigger(c, cb):
    admin_session[cb.from_user.id] = {"mode": "searching_user", "menu_id": cb.message.id}
    await cb.message.edit_text(
        "<b>🔍 Enter User ID:</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_users")]]),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"addmoney_(\d+)"))
async def add_money_trigger(c, cb):
    target_id = cb.data.split("_")[1]
    admin_session[cb.from_user.id] = {"mode": "adding_balance", "target_id": target_id, "menu_id": cb.message.id}
    await cb.message.edit_text(
        f"<b>➕ Add Balance to `{target_id}`</b>\n\n"
        "Enter the amount in INR (Numbers only):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_users")]]),
        parse_mode=enums.ParseMode.HTML
    )

#  Deduct Money UI Handler
@Client.on_callback_query(filters.regex(r"deductmoney_(\d+)"))
async def deduct_money_trigger(c, cb):
    target_id = cb.data.split("_")[1]
    admin_session[cb.from_user.id] = {"mode": "deducting_balance", "target_id": target_id, "menu_id": cb.message.id}
    await cb.message.edit_text(
        f"<b>➖ Deduct Balance from `{target_id}`</b>\n\n"
        "Enter the amount in INR to DEDUCT (Numbers only):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_users")]]),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"ban_(\d+)"))
async def ban_user_callback(c, cb):
    target_id = int(cb.data.split("_")[1])
    await col_users.update_one({"_id": target_id}, {"$set": {"is_banned": True}})
    await cb.answer(f"🚫 User {target_id} Banned!", show_alert=True)
    await safe_show_dashboard(c, cb)

# Unban User Handler
@Client.on_callback_query(filters.regex(r"unban_(\d+)"))
async def unban_user_callback(c, cb):
    target_id = int(cb.data.split("_")[1])
    await col_users.update_one({"_id": target_id}, {"$set": {"is_banned": False}})
    await cb.answer(f"✅ User {target_id} Unbanned!", show_alert=True)
    await safe_show_dashboard(c, cb)


# ==================================================================
# 💰 PAYMENTS (Crypto / Manual)
# ==================================================================

@Client.on_callback_query(filters.regex("admin_payments"))
async def admin_payments_menu(c, cb):
    clear_session(cb.from_user.id)
    # Fetch ALL pending payments 
    cursor = col_payments.find({"status": "pending"})
    txns = await cursor.to_list(length=5)
    
    if not txns:
        return await cb.answer("✅ All caught up! No pending payments.", show_alert=True)
    
    txn = txns[0] 
    # Safe retrieval of fields
    amount = txn.get("amount", "Unknown")
    method = txn.get("method", "Manual").upper()
    
    text = (
        "<b>💰 PENDING PAYMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: `{txn['user_id']}`\n"
        f"💵 Amount: ₹{amount}\n"
        f"🪙 Method: {method}\n"
        f"📅 Date: {txn.get('date', 'N/A')}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Check Admin Group for screenshot/proof."
    )
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"pay_approve_{txn['_id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"pay_reject_{txn['_id']}")
        ],
        [InlineKeyboardButton("➕ Manual Balance (UTR)", callback_data="manual_bal_input")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
    ])
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


# ==================================================================
# 📢 BROADCAST
# ==================================================================

@Client.on_callback_query(filters.regex("admin_broadcast"))
async def bc_menu(c, cb):
    clear_session(cb.from_user.id)
    text = "<b>📢 BROADCAST CENTER</b>\n\nSelect message type:"
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Simple Message", callback_data="bc_type_simple")],
        [InlineKeyboardButton("📌 Pin Message", callback_data="bc_type_pin")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
    ])
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"bc_type_(.+)"))
async def bc_input_trigger(c, cb):
    b_type = cb.data.split("_")[2]
    admin_session[cb.from_user.id] = {"mode": "broadcasting", "type": b_type, "menu_id": cb.message.id}
    await cb.message.edit_text(
        f"<b>📢 Broadcast ({b_type.upper()})</b>\n\n"
        "Send the message (Text/Media/Forward) you want to send to ALL users.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_broadcast")]]),
        parse_mode=enums.ParseMode.HTML
    )

# ==================================================================
# ⚙️ SETTINGS & FORCE SUB (Updated for Multiple Channels)
# ==================================================================
@Client.on_callback_query(filters.regex("admin_settings"))
async def settings_menu(c, cb):
    clear_session(cb.from_user.id)
    
    # fSub Multiple Status
    fsubs = await get_fsub_list()
    if fsubs:
        count = len(fsubs)
        # Show first ID as sample
        first_id = fsubs[0].get("_id", "Unknown")
        fsub_text = f"✅ Active ({count} Channels)\n🆔 Main: {first_id}"
    else:
        fsub_text = "❌ Inactive"
    
    # USDT Rate
    settings = await col_settings.find_one({"_id": "main_config"}) or {}
    usdt_rate = settings.get("usdt_rate", 90.0)
    maintenance = settings.get("maintenance", False)
    m_status = "🔴 ON" if maintenance else "🟢 OFF"
    
    text = (
        "<b>⚙️ BOT SETTINGS & CONFIG</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>USDT Rate:</b> ₹{usdt_rate}\n"
        f"🚧 <b>Maintenance:</b> {m_status}\n"
        f"📢 <b>Force Sub:</b>\n{fsub_text}\n\n"
        "<i>Select a setting to modify:</i>"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Add FSub Channel", callback_data="set_fsub_input")],
        [InlineKeyboardButton("🗑 Clear All FSubs", callback_data="remove_fsub")],
        [InlineKeyboardButton("💵 Set USDT Rate", callback_data="set_usdt_input")],
        [InlineKeyboardButton(f"🚧 Toggle Maintenance", callback_data="toggle_maint")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
    ])
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex("set_fsub_input"))
async def set_fsub_trigger(c, cb):
    admin_session[cb.from_user.id] = {"mode": "setting_fsub", "menu_id": cb.message.id}
    await cb.message.edit_text(
        "<b>📢 ADD FORCE SUBSCRIBE CHANNEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "1. Add me to the Channel/Group as Admin.\n"
        "2. Send the <b>Channel ID</b> here (e.g., -100xxxx).\n\n"
        "<i>I will auto-generate the invite link and add it to the list.</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_settings")]]),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_callback_query(filters.regex("remove_fsub"))
async def remove_fsub_action(c, cb):

    await del_fsub(None) # None
    
    from database import col_fsub
    await col_fsub.delete_many({})
    
    await cb.answer("✅ All Force Sub Channels Cleared!", show_alert=True)
    await settings_menu(c, cb)

@Client.on_callback_query(filters.regex("toggle_maint"))
async def toggle_maintenance_action(c, cb):
    current = await get_maintenance()
    new_status = not current
    await set_maintenance(new_status)
    status_text = "Enabled" if new_status else "Disabled"
    await cb.answer(f"✅ Maintenance {status_text}!", show_alert=True)
    await settings_menu(c, cb)

@Client.on_callback_query(filters.regex("set_usdt_input"))
async def set_usdt_trigger(c, cb):
    admin_session[cb.from_user.id] = {"mode": "setting_usdt", "menu_id": cb.message.id}
    await cb.message.edit_text(
        "<b>💵 SET USDT RATE</b>\n\n"
        "Send the new rate for <b>1 USDT</b> in INR.\n"
        "Example: <code>92.5</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_settings")]])
    )

# ==================================================================
# 🤖 THE MASTER LISTENER (State Managed)
# ==================================================================

@Client.on_message(filters.user(ADMINS) & ~filters.command(["admin", "start", "ping"]), group=2)
async def admin_master_listener(c, msg):
    user_id = msg.from_user.id
    if user_id not in admin_session:
        return # Not in admin state, ignore
    
    state = admin_session[user_id]
    mode = state.get("mode")
    menu_id = state.get("menu_id")

    try:
        # 1. SEARCH USER INPUT
        if mode == "searching_user" and msg.text:
            try:
                target_id = int(msg.text)
                user = await get_user(target_id)
                await msg.delete() # Cleanup
                
                if user:
                    info = (
                        f"👤 <b>User Found:</b> {user.get('name')}\n"
                        f"🆔 ID: `{target_id}`\n"
                        f"💰 Balance: ₹{user.get('balance', 0)}\n"
                        f"📅 Join: {user.get('join_date')}"
                    )
                    
                    is_banned = user.get("is_banned", False)
                    ban_btn = InlineKeyboardButton("✅ Unban User", callback_data=f"unban_{target_id}") if is_banned else InlineKeyboardButton("🚫 Ban User", callback_data=f"ban_{target_id}")
                    
                    btns = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("➕ Add", callback_data=f"addmoney_{target_id}"),
                            InlineKeyboardButton("➖ Deduct", callback_data=f"deductmoney_{target_id}")
                        ],
                        [ban_btn],
                        [InlineKeyboardButton("🔙 Back", callback_data="admin_users")]
                    ])

                    await c.edit_message_text(msg.chat.id, menu_id, info, reply_markup=btns, parse_mode=enums.ParseMode.HTML)
                else:
                    temp = await msg.reply("❌ User not found!")
                    await asyncio.sleep(2); await temp.delete()
            except: pass

        # 2. ADD BALANCE INPUT
        elif mode == "adding_balance" and msg.text:
            try:
                amount = int(msg.text)
                target = int(state["target_id"])
                await update_balance(target, amount)
                await msg.delete()
                
                await c.edit_message_text(msg.chat.id, menu_id, f"✅ Added ₹{amount} to User `{target}`", 
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Dashboard", callback_data="admin_home")]]), parse_mode=enums.ParseMode.HTML)
                clear_session(user_id)
            except: pass

        # 3. BROADCAST EXECUTION 
        elif mode == "broadcasting":
            status = await msg.reply("🚀 <b>Broadcasting...</b>")
            users = col_users.find({})
            count = 0
            
            should_pin = state.get("type") == "bc_type_pin" # Check logic 
            

            async for u in users:
                try:
                    # Copy returns the sent message object
                    sent_msg = await msg.copy(u["_id"])
                    
                    #  Pin Logic
                    if should_pin and sent_msg:
                        try:
                            await sent_msg.pin(both_sides=True)
                        except: pass # Ignore
                        
                    count += 1
                    await asyncio.sleep(0.05) # Rate limit safety
                except: pass
            
            await status.edit_text(f"✅ <b>Broadcast Complete!</b>\nSent to {count} users.")
            clear_session(user_id)


        # 4. SMART STOCK INPUT
        elif mode == "smart_input" and msg.text:
            parts = msg.text.split(" ")
            if len(parts) < 3: 
                temp = await msg.reply("❌ Format: `Country Price Year`")
                await asyncio.sleep(3)
                await temp.delete()
                return
            
            try:
                # Import here to avoid circular dependency
                from plugins.stock import manual_activate_upload
                raw_country, price, year = parts[0], int(parts[1]), parts[2]
                
                # Flag logic
                try:
                    matches = pycountry.countries.search_fuzzy(raw_country)
                    flag = "".join([chr(ord(c) + 127397) for c in matches[0].alpha_2]) if matches else "🏳️"
                    final_country = matches[0].name if matches else raw_country
                except:
                    final_country = raw_country
                    flag = "🏳️"

                await msg.delete()
                menu_msg = await c.get_messages(msg.chat.id, menu_id)
                await manual_activate_upload(c, menu_msg, final_country, price, year, flag, user_id)
            except Exception as e:
                print(f"Stock Input Error: {e}")

        elif mode == "deducting_balance" and msg.text:
            try:
                amount = int(msg.text)
                target = int(state["target_id"])
                
                await update_balance(target, -amount)
                await msg.delete()
                
                await c.edit_message_text(msg.chat.id, menu_id, f"✅ Deducted ₹{amount} from User `{target}`", 
                                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Dashboard", callback_data="admin_home")]]), parse_mode=enums.ParseMode.HTML)
                clear_session(user_id)
            except Exception as e:
                pass
        
        # 5. FORCE SUB CONFIGURATION
        elif mode == "setting_fsub" and msg.text:
            try:
                channel_id = int(msg.text)
                await msg.delete()
                
                # Verify Bot Admin status & Generate Link
                try:
                    chat = await c.get_chat(channel_id)
                    link = await c.export_chat_invite_link(channel_id)
                    # Multiple FSubs with Title
                    await update_fsub(channel_id, link, chat.title)
                    
                    success_text = (
                        f"✅ <b>Force Sub Added!</b>\n"
                        f"📢 <b>Channel:</b> {chat.title}\n"
                        f"🔗 <b>Link:</b> {link}\n\n"
                        "<i>Users must join ALL active channels.</i>"
                    )
                    await c.edit_message_text(
                        msg.chat.id, menu_id, success_text,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_settings")]])
                    )
                    clear_session(user_id)
                except Exception as e:
                    temp = await msg.reply(f"❌ Error: Make sure I am Admin there!\n{e}")
                    await asyncio.sleep(5); await temp.delete()

            except ValueError:
                temp = await msg.reply("❌ Invalid ID! Must be an integer (e.g. -100...)")
                await asyncio.sleep(3); await temp.delete()
        
        # 6. USDT RATE SETTING
        elif mode == "setting_usdt" and msg.text:
            try:
                rate = float(msg.text)
                await update_usdt_rate(rate)
                await msg.delete()
                await c.edit_message_text(
                    msg.chat.id, menu_id, f"✅ <b>USDT Rate Updated:</b> ₹{rate}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_settings")]])
                )
                clear_session(user_id)
            except ValueError:
                pass # Ignore invalid input

    except Exception as e:
        print(f"Master Listener Error: {e}")

@Client.on_callback_query(filters.regex("close_admin"))
async def close_admin_panel(c, cb):
    await cb.message.delete()
    clear_session(cb.from_user.id)
