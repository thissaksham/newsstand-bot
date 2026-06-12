import asyncio
from dotenv import load_dotenv
load_dotenv()
from database.operations import get_user_subscriptions, _get_client

async def main():
    db = await _get_client()
    # Subscribe my test user to a title that actually exists!
    titles = await db.table("titles").select("id").limit(1).execute()
    tid = titles.data[0]["id"]
    await db.table("users").upsert({"user_id": 12345, "first_name": "Test"}).execute()
    await db.table("subscriptions").upsert({"user_id": 12345, "title_id": tid}).execute()
    subs = await get_user_subscriptions('', 12345)
    print("Subs:", subs)

asyncio.run(main())
