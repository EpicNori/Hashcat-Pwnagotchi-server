from app import app


if __name__ == "__main__":
    import os

    debug_enabled = str(os.environ.get("HASHCAT_WPA_DEBUG", "")).lower() in ("1", "true", "yes", "on")
    port = int(os.environ.get("PORT", "9111"))
    app.run(host='0.0.0.0', port=port, debug=debug_enabled)
