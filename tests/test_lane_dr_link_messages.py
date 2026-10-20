"""Every refusal of a link says why in the server's own sentence (lane DR, decision 2).

Human decision 2026-10-20: a private file is answered with "이 파일은 공유
설정이 '링크가 있는 모든 사용자'가 아니라서 가져올 수 없어요. 공유 설정을
바꾸거나 파일을 내려받아 올려 주세요." -- and a file too large, a file that
is not a format the upload reads, and an address that is not Google Drive each
have a sentence of their own, in 해요체. The answer's ``detail`` carries the
code (for the screen) and the sentence (to show as it is). Google is faked.
"""

from __future__ import annotations

import unittest

import httpx

from tests.test_lane_dr_link_import import (
    DOC_LINK,
    DRIVE_LINK,
    SHEET_LINK,
    LinkServerCase,
    drive_download,
)
from voiney_lab import protocol_link_import as link_module

PRIVATE = (
    "이 파일은 공유 설정이 '링크가 있는 모든 사용자'가 아니라서 가져올 수 없어요. "
    "공유 설정을 바꾸거나 파일을 내려받아 올려 주세요."
)


class RefusalSentenceTests(LinkServerCase):
    async def refused(self, url, status: int, code: str, **extra) -> str:
        response = await self.import_link(url, **extra)
        self.assertEqual(response.status_code, status, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], code)
        return detail["message"]

    async def test_a_private_file_is_answered_with_the_decided_sentence(self) -> None:
        self.google.redirect(
            drive_download(),
            "https://accounts.google.com/ServiceLogin?service=wise&continue=x", status=302)
        message = await self.refused(DRIVE_LINK, 403, "link_private")
        self.assertEqual(message, PRIVATE)

    async def test_a_file_too_large_names_the_upload_s_limit(self) -> None:
        self.google.file(drive_download(), b"%PDF-1.7\n", content_type="application/pdf",
                         headers={"content-length": str(link_module.MAX_PROTOCOL_PDF_BYTES + 1)})
        message = await self.refused(DRIVE_LINK, 413, "link_too_large")
        self.assertEqual(
            message,
            "파일이 너무 커서 가져올 수 없어요. 64MB 까지 가져올 수 있어요. "
            "파일 크기를 줄이거나 나눠서 올려 주세요.")

    async def test_a_reagent_table_too_large_names_the_table_limit(self) -> None:
        async with self.client() as client:
            from tests.test_lane_dr_link_import import pdf_bytes, sheet_export
            registered = await client.post(
                "/api/protocols?filename=bench.pdf", content=pdf_bytes(),
                headers={"Content-Type": "application/pdf"})
        protocol_id = registered.json()["protocol"]["protocol_id"]
        self.google.answer(sheet_export(), httpx.Response(
            200, content=b"PK", headers={
                "content-length": str(link_module.MAX_REAGENT_TABLE_BYTES + 1)}))
        message = await self.refused(SHEET_LINK, 413, "link_too_large", protocol_id=protocol_id)
        self.assertIn("5MB 까지", message)

    async def test_a_file_of_another_format_says_which_formats_are_read(self) -> None:
        self.google.file(drive_download(), b"just some text\n", content_type="text/plain")
        message = await self.refused(DRIVE_LINK, 415, "link_unsupported_format")
        self.assertEqual(
            message,
            "이 파일은 PDF·Word 문서·사진(JPG·PNG·HEIC)이 아니라서 가져올 수 없어요. "
            "이 형식으로 저장해 링크를 넣거나 파일을 올려 주세요.")

    async def test_an_address_that_is_not_google_drive_says_so(self) -> None:
        for url in ("https://example.com/protocol.pdf", "http://drive.google.com/file/d/x/view",
                    "drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view", ""):
            with self.subTest(url=url):
                message = await self.refused(url, 422, "link_not_google_drive")
                self.assertEqual(
                    message,
                    "구글 드라이브·구글 문서·구글 시트의 링크만 가져올 수 있어요. "
                    "주소가 https://drive.google.com 이나 https://docs.google.com 으로 "
                    "시작하는지 확인해 주세요.")
        self.assertEqual(self.google.requests, [])

    async def test_the_other_refusals_each_have_their_sentence(self) -> None:
        cases = (
            ("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQr", {}, 422,
             "link_not_a_file", "이 링크는 파일 하나를 가리키지 않아요."),
            (SHEET_LINK, {}, 422, "link_sheet_needs_protocol",
             "구글 시트는 프로토콜에 시약 표로만 붙일 수 있어요."),
            (DOC_LINK, {"protocol_id": "protocol-0123456789abcdef"}, 415,
             "link_not_a_reagent_table", "시약 표로는 구글 시트나 엑셀(.xlsx) 파일만"),
        )
        for url, extra, status, code, start in cases:
            with self.subTest(code=code):
                message = await self.refused(url, status, code, **extra)
                self.assertTrue(message.startswith(start), message)

    async def test_every_code_has_a_sentence_in_haeyo_style(self) -> None:
        for code in link_module.LINK_IMPORT_STATUS:
            with self.subTest(code=code):
                message = link_module.LinkImportError(code).detail()["message"]
                self.assertTrue(message.endswith(("요.", "세요.")), message)
                self.assertNotIn("습니다", message)
                self.assertNotIn("니다.", message)


class FetchRefusalSentenceTests(LinkServerCase):
    async def test_not_found_timeout_and_a_failed_fetch(self) -> None:
        cases = (
            (httpx.Response(404, content=b"<html></html>"), 404, "link_not_found",
             "이 링크의 파일을 찾지 못했어요."),
            (httpx.ReadTimeout("slow"), 504, "link_timeout",
             "구글 드라이브가 제때 답하지 않아 가져오지 못했어요."),
            (httpx.Response(500), 502, "link_fetch_failed",
             "구글 드라이브에서 파일을 받지 못했어요."),
            (httpx.Response(302, headers={"location": "https://evil.example/x"}), 502,
             "link_redirect_refused", "구글 드라이브가 허용하지 않은 주소로 넘겨줘서"),
        )
        for answer, status, code, start in cases:
            with self.subTest(code=code):
                self.google.answer(drive_download(), answer)
                response = await self.import_link(DRIVE_LINK)
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json()["detail"]["code"], code)
                self.assertTrue(response.json()["detail"]["message"].startswith(start))


class SignInSentenceTests(LinkServerCase):
    signed_in = False

    async def test_nobody_signed_in(self) -> None:
        response = await self.import_link(DRIVE_LINK)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], {
            "code": "link_sign_in_required",
            "message": "로그인한 뒤에 링크로 가져올 수 있어요.",
        })


if __name__ == "__main__":
    unittest.main()
