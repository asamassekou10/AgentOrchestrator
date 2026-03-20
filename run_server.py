#!/usr/bin/env python3
"""Start the Virtual Band web UI."""

import uvicorn


def main():
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, reload=True, log_level="info")


if __name__ == "__main__":
    main()
