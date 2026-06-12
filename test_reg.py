import asyncio
from dotenv import load_dotenv
load_dotenv()
from database.operations import register_user

async def main():
    try: 
        await register_user('', 123456789, 'test', 'test')
        print("Success")
    except Exception as e: 
        print(repr(e))
asyncio.run(main())
