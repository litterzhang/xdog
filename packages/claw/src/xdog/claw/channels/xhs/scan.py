"""One public SDK QR attempt; the isolated candidate is published by the login owner."""
from __future__ import annotations

import importlib
import logging
import sys


def main() -> int:
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        try:
            login = importlib.import_module('xhs_cli.qr_login')
            browser = importlib.import_module('xhs_cli.browser')
            cookies = importlib.import_module('xhs_cli.cookies')
            errors = importlib.import_module('xhs_cli.exceptions')
        except ImportError:
            print('XHS requires the modified xiaohongshu-cli[browser] dependency; sync the xhs extra.', file=sys.stderr)
            return 1
        try:
            # Public API owns QR/browser flow and account verification. It does
            # not save credentials. This process writes only its isolated HOME.
            result = login.qrcode_login(timeout_s=240)
            values = result.cookies
            if not isinstance(values, dict) or not all(values.get(key) for key in ('a1', 'web_session')):
                print('XHS SDK returned incomplete authenticated credentials.', file=sys.stderr)
                return 1
            cookies.save_cookies(values)
            print('XHS session verified; preparing the group account binding...')
            return 0
        except (login.QrLoginError, browser.BrowserUnavailable, errors.XhsApiError) as exc:
            # These public fork errors are credential-free by contract; never
            # inspect private SDK fields or parse completion response prose.
            print(str(exc), file=sys.stderr)
            return 1
        except Exception:
            print('XHS SDK login/export failed; existing group credentials were not replaced.', file=sys.stderr)
            return 1
    finally:
        logging.disable(previous_logging)


if __name__ == '__main__':
    raise SystemExit(main())
