import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

if __name__ == "__main__":
    import uvicorn

    print()
    print("=" * 52)
    print("  Quant Trading Dashboard")
    print("  Open in your browser: http://localhost:8000")
    print("=" * 52)
    print()

    from src.api import app
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
