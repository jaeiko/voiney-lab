#!/usr/bin/env python3
"""Invite links and accounts of the pilot, from the server's shell (lane ON, decision ①).

With ``VOINEY_LAB_AUTH_MODE=accounts`` nobody can join the pilot without an
invite link, and this tool is the only thing that makes one. It works on the
account store beside the pilot's workspace (``accounts.sqlite`` in
``data/runtime/pilot/workspace``, or an explicit ``--data-dir``)
and never on anything else.

    python scripts/pilot_invite.py --base-url https://pilot.example.org
    python scripts/pilot_invite.py --admin --hours 24 --note "최수진"
    python scripts/pilot_invite.py --list
    python scripts/pilot_invite.py --revoke invite-0123456789abcdef
    python scripts/pilot_invite.py --accounts
    python scripts/pilot_invite.py --disable "김재준"      # or --enable

A link is shown once and never kept: the store holds only its hash, its
expiry (``--hours``, 72 by default), the lab it joins (``--lab``), whether
the account will be an administrator (``--admin``) and a note for the person
who made it. One link makes one account. Switching an account off ends its
sessions at once; an administrator can do the same from the screen.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data" / "runtime" / "pilot" / "workspace"
DEFAULT_BASE_URL = "http://127.0.0.1:8080"


def data_dir_from(argument: str | None) -> Path:
    """The explicit folder, otherwise the pilot launcher's fixed workspace."""

    if argument:
        return Path(argument).expanduser().resolve()
    return DEFAULT_DATA_DIR


def _yes_no(value: bool) -> str:
    return "예" if value else "아니오"


def invite_state(invite, now) -> str:
    if invite.revoked_at is not None:
        return "취소됨"
    if invite.used_at is not None:
        return "사용됨"
    if invite.expires_at <= now:
        return "만료됨"
    return "열림"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="파일럿 초대 링크를 만들고, 계정을 보고 끈다.")
    parser.add_argument("--data-dir", help=f"작업공간 데이터 폴더 (기본: {DEFAULT_DATA_DIR}; 셸 설정은 쓰지 않음)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"링크의 주소 앞부분 (기본 {DEFAULT_BASE_URL}; HTTPS 를 두면 그 주소)")
    parser.add_argument("--lab", default=None, help="가입할 연구실 id (기본 lab-pilot)")
    parser.add_argument("--admin", action="store_true", help="이 링크로 만든 계정은 관리자")
    parser.add_argument("--hours", type=float, default=None, help="링크 유효 시간 (기본 72)")
    parser.add_argument("--note", default="", help="누구에게 주는 링크인지 (기록용)")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--list", action="store_true", help="초대 목록")
    actions.add_argument("--revoke", metavar="INVITE_ID", help="초대 취소")
    actions.add_argument("--accounts", action="store_true", help="계정 목록")
    actions.add_argument("--disable", metavar="NAME", help="계정 끄기 (세션 종료)")
    actions.add_argument("--enable", metavar="NAME", help="계정 다시 켜기")
    arguments = parser.parse_args(argv)

    from voiney_lab import accounts as acc

    folder = data_dir_from(arguments.data_dir)
    path = folder / acc.ACCOUNTS_DATABASE_FILENAME
    listing_only = arguments.list or arguments.accounts
    if listing_only and not path.is_file():
        print(f"계정 저장소가 아직 없습니다: {path}", file=sys.stdout)
        return 0
    try:
        store = acc.AccountStore.open(path)
    except acc.AccountError as exc:
        print(f"[REFUSED] {exc} ({path})", file=sys.stderr)
        return 2
    try:
        now = store._now().isoformat()  # noqa: SLF001 - the store's own clock, for the listing
        if arguments.list:
            invites = store.list_invites()
            if not invites:
                print("초대 없음")
            for invite in invites:
                print(f"{invite.invite_id}  연구실 {invite.organization_id}  관리자 {_yes_no(invite.admin)}  "
                      f"만료 {invite.expires_at}  {invite_state(invite, now)}"
                      f"{'  ' + invite.note if invite.note else ''}")
            return 0
        if arguments.accounts:
            listed = store.list_accounts()
            if not listed:
                print("계정 없음")
            for account in listed:
                print(f"{account.name}  연구실 {account.organization_id}  관리자 {_yes_no(account.admin)}  "
                      f"이용 권한 {account.entitlement}  {'꺼짐' if account.disabled else '켜짐'}  "
                      f"만든 날 {account.created_at}")
            return 0
        if arguments.revoke:
            try:
                invite = store.revoke_invite(arguments.revoke)
            except acc.AccountError as exc:
                print(f"[REFUSED] {exc}", file=sys.stderr)
                return 2
            print(f"초대 {invite.invite_id} 을(를) 취소했습니다.")
            return 0
        if arguments.disable or arguments.enable:
            name = arguments.disable or arguments.enable
            account = store.account_by_name(name)
            if account is None:
                print(f"[REFUSED] 그런 계정이 없습니다: {acc.normalize_name(name)}", file=sys.stderr)
                return 2
            changed = store.set_disabled(account.account_id, bool(arguments.disable))
            if changed.disabled:
                print(f"계정 '{changed.name}' 을(를) 껐습니다 (열려 있던 세션은 끝났습니다).")
            else:
                print(f"계정 '{changed.name}' 을(를) 다시 켰습니다.")
            return 0
        try:
            token, invite = store.create_invite(
                organization_id=arguments.lab or acc.DEFAULT_ORGANIZATION_ID,
                admin=arguments.admin,
                hours=acc.INVITE_DEFAULT_HOURS if arguments.hours is None else arguments.hours,
                note=arguments.note,
            )
        except acc.AccountError as exc:
            print(f"[REFUSED] {exc}", file=sys.stderr)
            return 2
        base = arguments.base_url.rstrip("/")
        hours = acc.INVITE_DEFAULT_HOURS if arguments.hours is None else arguments.hours
        print(f"초대 링크 (지금 한 번만 보입니다 · {hours:g}시간 뒤 만료 · 한 번 쓰면 끝):")
        print(f"  {base}/?invite={token}")
        print(f"초대 id {invite.invite_id} · 연구실 {invite.organization_id} · 관리자 {_yes_no(invite.admin)} · "
              f"이용 권한 {invite.entitlement} · 만료 {invite.expires_at}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
