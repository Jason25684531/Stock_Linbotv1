"""Legacy facade preserving historical app.py entrypoints."""

from app import (
    app,
    callback,
    handle_message,
    handler,
    login_manager,
    main,
    postback_handler,
    reply_message,
)


__all__ = [
    'app',
    'callback',
    'handle_message',
    'handler',
    'login_manager',
    'main',
    'postback_handler',
    'reply_message',
]


if __name__ == '__main__':
    raise SystemExit(main())