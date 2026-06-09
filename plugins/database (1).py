import datetime
import motor.motor_asyncio
from bson.objectid import ObjectId
from config import MONGO_URI

# ==================================================================
# 🔌 DATABASE CONNECTION
# ==================================================================
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client["SimpleStoreUltimate"]

# Collections
col_users = db["users"]
col_stock = db["stock"]
col_orders = db["orders"]
col_payments = db["payments"]
col_fsub = db['fsub'] 
col_settings = db['settings']
col_coupons = db['coupons']
# ==================================================================
# 👤 1. USER MANAGEMENT
# ==================================================================

async def add_user(user_id, name):
    user = await col_users.find_one({"_id": user_id})
    if not user:
        await col_users.insert_one({
            "_id": user_id,
            "name": name,
            "balance": 0.0,
            "total_deposit": 0.0,
            "terms_accepted": False,
            "join_date": datetime.datetime.now()
        })

async def get_user(user_id):
    return await col_users.find_one({"_id": user_id})

async def update_balance(user_id, amount):
    """
    Updates user balance. 
    Amount can be positive (Deposit) or negative (Purchase).
    """
    await col_users.update_one(
        {"_id": user_id},
        {"$inc": {"balance": amount}}
    )
    if amount > 0:
        await col_users.update_one(
            {"_id": user_id},
            {"$inc": {"total_deposit": amount}}
        )

# ==================================================================
# 📦 2. STOCK MANAGEMENT (UNIFIED VIEW)
# ==================================================================

async def add_stock(category, items_list):
    if not items_list: return 0
    
    for item in items_list:
        item["category"] = category 
        item["date_added"] = datetime.datetime.now()
    
    result = await col_stock.insert_many(items_list)
    return len(result.inserted_ids)

async def get_unique_buckets(target_type=None):
    """
     UNIFIED AGGREGATION:
    Groups ALL fresh stock by Country + Price + Year.
    Ignores 'target_type' filter to show TOTAL stock in both menus.
    """
    pipeline = [
        {"$match": {"status": "fresh"}}, # No type filter
        {"$group": {
            "_id": {
                "country": "$country",
                "price": "$price",
                "year": "$year",
                "flag": "$flag",
                "type": "$type" # Group by type too, 
            },
            "count": {"$sum": 1},
            "sample_id": {"$first": "$_id"} # Pick one ID to use as a handle
        }},
        {"$sort": {"_id.country": 1}}
    ]
    return await col_stock.aggregate(pipeline).to_list(length=None)

async def get_stock_stats(target_type="accounts"):
    """
    Format grouped data for the UI List.
    Calls get_unique_buckets which returns EVERYTHING.
    """
    buckets = await get_unique_buckets(target_type)
    stats = []
    for b in buckets:
        stats.append({
            "_id": str(b["sample_id"]), # Convert ObjectId 
            "country": b["_id"]["country"],
            "price": b["_id"]["price"],
            "year": b["_id"]["year"],
            "flag": b["_id"].get("flag", "🏳️"),
            "count": b["count"],
            "type": b["_id"].get("type", "session")
        })
    return stats

async def get_product_details(product_id):
    """
    Fetches details of a sample product. 
    Required by buy.py to show confirmation screen.
    """
    try:
        return await col_stock.find_one({"_id": ObjectId(product_id)})
    except:
        return None

async def get_stock_count(country, item_type, price, year):
    """
    Double checks real-time stock before purchase.
    """
    return await col_stock.count_documents({
        "country": country,
        "type": item_type,
        "price": price,
        "year": year,
        "status": "fresh"
    })

# ==================================================================
# 🛒 3. BUYING LOGIC (Atomic Transactions)
# ==================================================================

async def buy_item_atomic(user_id, product_id, category):
    """
    Atomically buys an item:
    1. Selects a fresh item.
    2. Deducts balance.
    3. Moves item to sold status.
    4. Creates order record.
    """
    from bson import ObjectId
    import datetime

    # 1. Fetch Item & User
    try:
        item = await col_stock.find_one({"_id": ObjectId(product_id), "status": "fresh"})
    except:
        item = await col_stock.find_one({"_id": product_id, "status": "fresh"})
        
    if not item: return None

    user = await col_users.find_one({"_id": user_id})
    if not user: return None

    price = item["price"]
    balance = user.get("balance", 0)
    if isinstance(balance, str): balance = 0.0

    if balance < price: return None

    # 2. Start Transaction (Simulated)
    # Deduct Balance
    new_balance = balance - price
    await col_users.update_one({"_id": user_id}, {"$set": {"balance": new_balance}})

    # Mark Stock as Sold
    await col_stock.update_one(
        {"_id": item["_id"]},
        {"$set": {"status": "sold", "sold_to": user_id, "sold_at": datetime.datetime.utcnow()}}
    )

    # 3. Create Order Record
    order_data = {
        "user_id": user_id,
        "item_id": item["_id"],
        "data": item.get("data"),   # Session String
        "phone": item.get("phone"), # Phone Number
        "price": price,
        "country": item.get("country", "Unknown"),
        "flag": item.get("flag", "🏳️"),
        "type": "session" if category == "sessions" else "account",
        "date": datetime.datetime.utcnow(),
        "otp": None
    }
    
    result = await col_orders.insert_one(order_data)
    
    # Return complete order with ID
    order_data["_id"] = result.inserted_id
    return order_data


async def get_order(order_id):
    """Required for OTP Checking."""
    try:
        return await col_orders.find_one({"_id": ObjectId(order_id)})
    except:
        return None

# ==================================================================
# 💰 4. PAYMENTS & DEPOSITS
# ==================================================================

async def create_deposit(user_id, amount, utr, method, status="pending"):
    """
    Creates a deposit log.
    Returns 'duplicate' if UTR exists, else 'created'.
    """
    # Prevent duplicate UTRs
    if await get_deposit(utr):
        return "duplicate"

    await col_payments.insert_one({
        "user_id": user_id,
        "amount": amount,
        "utr": utr,
        "method": method,
        "status": status,
        "date": datetime.datetime.now()
    })
    return "created"

async def get_deposit(utr):
    """Checks if UTR exists."""
    return await col_payments.find_one({"utr": utr})

# ==================================================================
# 📦 2. STOCK MANAGEMENT (2-STEP MENU UPDATES)
# ==================================================================

async def get_unique_countries():
    """
    Step 1: Returns a list of all unique countries that have 'fresh' stock.
    Used to show the first layer of buttons.
    """
    pipeline = [
        {"$match": {"status": "fresh"}},
        {"$group": {
            "_id": "$country",
            "flag": {"$first": "$flag"}
        }},
        {"$sort": {"_id": 1}}
    ]
    cursor = col_stock.aggregate(pipeline)
    return await cursor.to_list(length=None)

async def get_buckets_by_country(country_name):
    """
    Step 2: Returns unique product buckets (Price/Year) for a specific country.
    Used when a user clicks on a country button.
    """
    pipeline = [
        {"$match": {"status": "fresh", "country": country_name}},
        {"$group": {
            "_id": {
                "price": "$price",
                "year": "$year",
                "flag": "$flag",
                "type": "$type"
            },
            "count": {"$sum": 1},
            "sample_id": {"$first": "$_id"}
        }},
        {"$sort": {"_id.price": 1}}
    ]
    buckets = await col_stock.aggregate(pipeline).to_list(length=None)
    
    
    stats = []
    for b in buckets:
        stats.append({
            "_id": str(b["sample_id"]),
            "country": country_name,
            "price": b["_id"]["price"],
            "year": b["_id"]["year"],
            "flag": b["_id"].get("flag", "🏳️"),
            "count": b["count"],
            "type": b["_id"].get("type", "session")
        })
    return stats





async def set_fsub(channel_id, link):
    await col_settings.update_one(
        {"_id": "fsub"},
        {"$set": {"channel_id": channel_id, "link": link}},
        upsert=True
    )

async def get_fsub():
    """Fallback for old code: Returns the first fsub channel."""
    return await col_fsub.find_one({})

# ==================================================================
# 📢 MULTI-FSUB MANAGEMENT (Unlimited Channels)
# ==================================================================

async def add_fsub(chat_id, invite_link, title):
    """Adds a new channel to the Force Sub list."""
    return await col_fsub.update_one(
        {"_id": chat_id},
        {"$set": {"link": invite_link, "title": title}},
        upsert=True
    )

async def get_fsub_list():
    """Returns a list of all configured FSub channels."""
    cursor = col_fsub.find({})
    return await cursor.to_list(length=100) # Unlimited (up to 100)

async def del_fsub(chat_id):
    """Removes a specific channel from FSub."""
    return await col_fsub.delete_one({"_id": chat_id})


async def update_fsub(chat_id, invite_link=None, title="Channel"):
    """
    Renamed to match admin.py import. 
    Adds or updates a channel in the FSub list.
    """
    if chat_id is None:

        return 
        
    return await col_fsub.update_one(
        {"_id": chat_id},
        {"$set": {"link": invite_link, "title": title}},
        upsert=True
    )

# ==================================================================
# 🎟️ REDEEM / COUPON SYSTEM
# ==================================================================

async def create_coupon(code: str, amount: int, limit: int):
    """Creates a new coupon code."""
    # coupon 
    await col_coupons.delete_one({"code": code})
    return await col_coupons.insert_one({
        "code": code,
        "amount": amount,
        "limit": limit,
        "used_count": 0,
        "used_by": [] # List of User IDs who used it
    })

async def get_coupon(code: str):
    """Fetches coupon details."""
    return await col_coupons.find_one({"code": code})

async def redeem_coupon_db(user_id, code):
    """
    Attempts to redeem a coupon atomically.
    Returns: (Success: Bool, Message: Str, Amount: Int)
    """
    coupon = await col_coupons.find_one({"code": code})
    
    if not coupon:
        return False, "❌ Invalid Code!", 0
        
    if coupon["used_count"] >= coupon["limit"]:
        return False, "❌ Coupon Limit Reached!", 0
        
    if user_id in coupon.get("used_by", []):
        return False, "⚠️ You have already used this coupon!", 0
        

    result = await col_coupons.update_one(
        {"code": code, "used_count": {"$lt": coupon["limit"]}, "used_by": {"$ne": user_id}},
        {
            "$push": {"used_by": user_id},
            "$inc": {"used_count": 1}
        }
    )
    
    if result.modified_count > 0:
        return True, "✅ Coupon Redeemed!", coupon["amount"]
    else:
        return False, "❌ Error: Coupon expired or used just now.", 0

# ==================================================================
# 🤝 REFERRAL SYSTEM (Milestone Based)
# ==================================================================

async def set_referrer(new_user_id, referrer_id):
    """
    Sets the referrer for a new user.
    Conditions: New user must not have a referrer already.
    """
    if new_user_id == referrer_id:
        return False 
        
    user = await col_users.find_one({"_id": new_user_id})
    
    
    if user and not user.get("referred_by"):
        await col_users.update_one(
            {"_id": new_user_id},
            {
                "$set": {
                    "referred_by": referrer_id,
                    "referral_paid": False # Bonus not paid yet
                }
            }
        )
        return True
    return False

async def check_referral_milestone(user_id, current_deposit_amount):
    """
    Checks if User reached ₹1000 total deposit.
    If yes, rewards the Referrer with ₹20.
    Returns: referrer_id (if bonus given), else None
    """
    user = await col_users.find_one({"_id": user_id})
    if not user or not user.get("referred_by"):
        return None
        
    if user.get("referral_paid"):
        return None # Already paid
        
    # Calculate Total Deposit including the new amount
    prev_total = user.get("total_deposit", 0)
    new_total = prev_total + current_deposit_amount
    
    
    if new_total >= 1000:
        referrer_id = user["referred_by"]
        
        # 1. 
        await update_balance(referrer_id, 20)
        
        # 2. 
        await col_users.update_one(
            {"_id": user_id},
            {"$set": {"referral_paid": True}}
        )
        return referrer_id
        
    return None


# ==================================================================
# 🚧 MAINTENANCE & GLOBAL SETTINGS
# ==================================================================

async def set_maintenance(status: bool):
    """Toggles Global Maintenance Mode."""
    return await col_settings.update_one(
        {"_id": "main_config"},
        {"$set": {"maintenance": status}},
        upsert=True
    )

async def get_maintenance():
    """Checks if Maintenance Mode is ON or OFF."""
    doc = await col_settings.find_one({"_id": "main_config"})
    return doc.get("maintenance", False) if doc else False

async def update_usdt_rate(rate: float):
    """Updates the 1 USDT = INR rate."""
    return await col_settings.update_one(
        {"_id": "main_config"},
        {"$set": {"usdt_rate": rate}},
        upsert=True
    )
