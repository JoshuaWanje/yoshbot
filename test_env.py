from dotenv import load_dotenv
import os

load_dotenv()
key = os.environ.get("OPENROUTER_API_KEY")
print("KEY FOUND:", repr(key))