import logging
import os

from dotenv import load_dotenv

load_dotenv()

from src.equity_research.tools.sec_api import fetch_latest_10k_text

logging.basicConfig(level=logging.INFO)

print("Contacting SEC EDGAR...")
print("User-Agent configured:", bool(os.getenv("SEC_USER_AGENT")))

risk_factors = fetch_latest_10k_text("AAPL")

if risk_factors:
    print("\n--- TEST SUCCESSFUL ---")
    print("Filing Extract Length:", len(risk_factors), "characters.")
    print("\nSample Text from Filing:")
    print(risk_factors[:1000])
else:
    print("\nTest failed. Make sure you set SEC_USER_AGENT in your .env file.")
