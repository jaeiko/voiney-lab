#!/usr/bin/env python3
"""Opt-in bounded Google STT authentication check; never prints a token or path."""
from __future__ import annotations

import argparse
import os
import time

from voiney_lab.configuration import GoogleSttV2Settings, GoogleSttV2ConfigurationError
from voiney_lab.voice_providers import _google_v2_token, SpeechProviderError
import requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=20, choices=range(1, 21),
                        help='total authentication deadline in seconds (1–20)')
    args = parser.parse_args()
    try:
        settings = GoogleSttV2Settings.from_environment(os.environ)
        _google_v2_token(settings, time.monotonic() + args.timeout)
    except GoogleSttV2ConfigurationError as exc:
        print('ERROR: ' + ','.join(exc.field_names))
        return 1
    except SpeechProviderError as exc:
        print('ERROR: google_stt_auth ' + exc.code)
        return 1
    except requests.RequestException:
        print('ERROR: google_stt_auth transport_failed')
        return 1
    print('OK: google_stt_auth (Speech API not called)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
