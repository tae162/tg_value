import os
from dotenv import load_dotenv

load_dotenv()

GOOGLE_CLOUD_KEY_PATH = os.getenv("GOOGLE_CLOUD_KEY_PATH")
GOOGLE_DOC_SHEET_FILE_TGVAL_KEY = os.getenv("GOOGLE_DOC_SHEET_FILE_TGVAL_KEY")
OPENDART_API_KEY = os.getenv("OPENDART_API_KEY")


def main():
    print(f"Key path: {GOOGLE_CLOUD_KEY_PATH}")
    print(f"Sheet key: {GOOGLE_DOC_SHEET_FILE_TGVAL_KEY}")
    print(f"OpenDart API key loaded: {'yes' if OPENDART_API_KEY else 'no'}")


if __name__ == "__main__":
    main()
