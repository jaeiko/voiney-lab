"""What a link download may and may not do, on a fake HTTP transport (lane DR, decision 4).

Human decision 2026-10-20: the allowed hosts, the refusals, redirects, the
size limit, the format read from the bytes, and a private file are pinned on
fake HTTP. Also pinned: no cookie, credential or Referer is sent; the client
ignores proxy settings in the environment; nothing reaches a private,
loopback, link-local or reserved address, whether a name resolves there or
the connection lands there; the download stops at its deadline.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from tests.test_lane_an_source_formats import docx_bytes, photo_bytes
from tests.test_lane_dr_link_import import (
    DOC_EXPORT_HOST,
    DOC_ID,
    DRIVE_LINK,
    FILE_ID,
    GOOGLE_ADDRESS,
    FakeGoogle,
    LinkServerCase,
    doc_export,
    drive_download,
    pdf_bytes,
    xlsx_bytes,
)
from voiney_lab import protocol_link_import as link_module
from voiney_lab.protocol_link_import import LinkImportError, fetch_google_link, parse_google_link


class LinkAllowListTests(unittest.TestCase):
    def refused(self, url: str) -> str:
        with self.assertRaises(LinkImportError) as caught:
            parse_google_link(url)
        return caught.exception.code

    def test_only_https_links_on_the_three_google_hosts_are_read(self) -> None:
        for url in (
            f"https://drive.google.com/file/d/{FILE_ID}/view",
            f"https://DRIVE.Google.com/file/d/{FILE_ID}/view",
            f"https://drive.google.com./file/d/{FILE_ID}/view",
            f"https://drive.google.com:443/file/d/{FILE_ID}/view",
            f"https://docs.google.com/document/d/{DOC_ID}/edit",
            f"https://drive.usercontent.google.com/download?id={FILE_ID}",
        ):
            with self.subTest(url=url):
                self.assertEqual(parse_google_link(url).kind,
                                 "document" if "document" in url else "drive_file")
        self.assertEqual(link_module.ALLOWED_LINK_HOSTS, frozenset({
            "drive.google.com", "docs.google.com", "drive.usercontent.google.com"}))

    def test_other_addresses_are_not_google_drive(self) -> None:
        for url in (
            f"http://drive.google.com/file/d/{FILE_ID}/view",
            f"https://drive.google.com.evil.example/file/d/{FILE_ID}/view",
            f"https://evil.example/drive.google.com/file/d/{FILE_ID}/view",
            f"https://xdrive.google.com/file/d/{FILE_ID}/view",
            f"https://drive.google.com@evil.example/file/d/{FILE_ID}/view",
            f"https://user:secret@drive.google.com/file/d/{FILE_ID}/view",
            f"https://drive.google.com:8443/file/d/{FILE_ID}/view",
            f"https://142.250.196.110/file/d/{FILE_ID}/view",
            f"https://[::1]/file/d/{FILE_ID}/view",
            f"https://{DOC_EXPORT_HOST}/export/a/b/{DOC_ID}?format=pdf",
            f"https://lh3.googleusercontent.com/d/{FILE_ID}",
            f"https://www.google.com/url?q=https://drive.google.com/file/d/{FILE_ID}/view",
            f"ftp://drive.google.com/file/d/{FILE_ID}",
            f"file:///etc/passwd",
            f"https://drive.google.com/file/d/{FILE_ID}/view\nHost: evil.example",
            "https://drive.google.com/" + "a" * 2100,
            None, 42, "",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.refused(url), "link_not_google_drive")

    def test_a_google_address_that_names_no_file(self) -> None:
        for url in (
            "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQr",
            "https://drive.google.com/drive/my-drive",
            "https://drive.google.com/",
            "https://docs.google.com/forms/d/1AbCdEfGhIjKlMnOpQr/viewform",
            "https://drive.google.com/file/d/short/view",
            "https://drive.google.com/open?id=../../etc",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.refused(url), "link_not_a_file")

    def test_slides_are_not_a_format_read(self) -> None:
        self.assertEqual(
            self.refused("https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQr/edit"),
            "link_unsupported_format")

    def test_the_file_id_alone_reaches_the_download_url(self) -> None:
        link = parse_google_link(
            f"https://drive.google.com/open?id={FILE_ID}&x=%0d%0aHost:evil&resourcekey=bad%20key")
        self.assertEqual(link.download_url, drive_download())


class FetchCase(unittest.TestCase):
    """fetch_google_link on fake Google, with a fake resolver."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.google = FakeGoogle()
        self.addresses: dict[str, list[str]] = {}
        self.resolved: list[str] = []
        self.patches = [
            patch.object(link_module, "_transport", lambda: self.google.transport()),
            patch.object(link_module, "_resolve", self._resolve),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def _resolve(self, host: str) -> list[str]:
        self.resolved.append(host)
        return self.addresses.get(host, [GOOGLE_ADDRESS])

    def fetch(self, url: str = DRIVE_LINK, *, reagent_table: bool = False, **kwargs):
        return fetch_google_link(
            parse_google_link(url), self.directory, reagent_table=reagent_table, **kwargs)

    def refused(self, url: str = DRIVE_LINK, **kwargs) -> str:
        with self.assertRaises(LinkImportError) as caught:
            self.fetch(url, **kwargs)
        return caught.exception.code

    def urls(self) -> list[str]:
        return [str(request.url) for request in self.google.requests]


class RedirectTests(FetchCase):
    def test_a_docs_export_is_followed_to_google_s_export_host(self) -> None:
        export = f"https://{DOC_EXPORT_HOST}/export/a/b/1/2/*/{DOC_ID}?format=pdf"
        self.google.redirect(doc_export(), export)
        self.google.file(export, pdf_bytes(), content_type="application/pdf")
        fetched = self.fetch(f"https://docs.google.com/document/d/{DOC_ID}/edit")
        self.assertEqual(fetched.final_host, DOC_EXPORT_HOST)
        self.assertEqual(self.urls(), [doc_export(), export])

    def test_drive_s_own_hand_on_to_its_download_host_is_followed(self) -> None:
        self.google.redirect(f"https://drive.google.com/uc?id={FILE_ID}&export=download",
                             drive_download(), status=303)
        self.google.file(drive_download(), pdf_bytes(), content_type="application/pdf")
        link = link_module.GoogleLink(DRIVE_LINK, "drive_file", FILE_ID)
        with patch.object(link_module.GoogleLink, "download_url",
                          f"https://drive.google.com/uc?id={FILE_ID}&export=download"):
            fetched = fetch_google_link(link, self.directory, reagent_table=False)
        self.assertEqual(fetched.final_host, "drive.usercontent.google.com")

    def test_a_redirect_elsewhere_is_refused_and_not_followed(self) -> None:
        for location in (
            "https://evil.example/file.pdf",
            "http://drive.usercontent.google.com/download?id=x",
            "https://lh3.googleusercontent.com/d/x",
            "https://doc-0g-3o-docstext.googleusercontent.com.evil.example/x",
            "https://drive.google.com:8443/x",
            "https://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
            "",
        ):
            with self.subTest(location=location):
                self.google.requests.clear()
                self.google.redirect(drive_download(), location, status=302)
                code = self.refused()
                self.assertIn(code, {"link_redirect_refused", "link_fetch_failed"})
                self.assertEqual(self.urls(), [drive_download()])

    def test_a_relative_redirect_stays_on_its_host(self) -> None:
        self.google.redirect(drive_download(), "/download?id=other&export=download")
        target = "https://drive.usercontent.google.com/download?id=other&export=download"
        self.google.file(target, pdf_bytes(), content_type="application/pdf")
        self.assertEqual(self.fetch().final_host, "drive.usercontent.google.com")
        self.assertEqual(self.urls()[-1], target)

    def test_a_redirect_loop_stops(self) -> None:
        self.google.redirect(drive_download(), drive_download())
        self.assertEqual(self.refused(), "link_redirect_refused")
        self.assertEqual(len(self.google.requests), link_module.MAX_REDIRECTS + 1)


class NoCredentialTests(FetchCase):
    def test_no_cookie_credential_or_referer_is_sent_on_any_hop(self) -> None:
        export = f"https://{DOC_EXPORT_HOST}/export/a/b/1/2/*/{DOC_ID}?format=pdf"
        self.google.redirect(doc_export(), export, headers={
            "set-cookie": "NID=secret; Domain=.google.com; Path=/; Secure"})
        self.google.file(export, pdf_bytes(), content_type="application/pdf",
                         headers={"set-cookie": "COMPASS=x; Path=/"})
        self.fetch(f"https://docs.google.com/document/d/{DOC_ID}/edit")
        self.assertEqual(len(self.google.requests), 2)
        for request in self.google.requests:
            with self.subTest(url=str(request.url)):
                for header in ("cookie", "authorization", "referer", "proxy-authorization"):
                    self.assertNotIn(header, request.headers)
                self.assertEqual(request.headers["user-agent"], "voiney-lab-link-import/1")

    def test_proxy_and_netrc_settings_in_the_environment_are_not_read(self) -> None:
        netrc = self.directory / "netrc"
        netrc.write_text("machine drive.usercontent.google.com login me password secret\n")
        self.google.file(drive_download(), pdf_bytes(), content_type="application/pdf")
        with patch.dict(os.environ, {
            "HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9",
            "NETRC": str(netrc),
        }):
            self.fetch()
        self.assertEqual(self.urls(), [drive_download()])
        self.assertNotIn("authorization", self.google.requests[0].headers)


class PrivateAddressTests(FetchCase):
    def test_a_google_name_that_resolves_to_a_private_address_is_not_asked(self) -> None:
        for address in ("127.0.0.1", "10.0.0.8", "192.168.1.1", "172.16.0.1",
                        "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1", "fd00::1",
                        "fe80::1%eth0", "::ffff:10.0.0.1", "224.0.0.1", "not-an-address"):
            with self.subTest(address=address):
                self.addresses["drive.usercontent.google.com"] = [GOOGLE_ADDRESS, address]
                self.assertEqual(self.refused(), "link_redirect_refused")
                self.assertEqual(self.google.requests, [])

    def test_a_redirect_host_that_resolves_privately_is_not_asked(self) -> None:
        export = f"https://{DOC_EXPORT_HOST}/export/x"
        self.addresses[DOC_EXPORT_HOST] = ["10.1.2.3"]
        self.google.redirect(doc_export(), export)
        self.assertEqual(self.refused(f"https://docs.google.com/document/d/{DOC_ID}/edit"),
                         "link_redirect_refused")
        self.assertEqual(self.urls(), [doc_export()])

    def test_a_connection_that_lands_on_a_private_address_is_not_read(self) -> None:
        class Stream:
            def get_extra_info(self, name):
                return ("10.0.0.8", 443) if name == "server_addr" else None

        self.google.answer(drive_download(), lambda request: httpx.Response(
            200, content=pdf_bytes(), extensions={"network_stream": Stream()}))
        self.assertEqual(self.refused(), "link_redirect_refused")
        self.assertFalse((self.directory / "linked.download").exists())

    def test_a_name_that_does_not_resolve(self) -> None:
        def fail(host):
            raise OSError("no such host")

        with patch.object(link_module, "_resolve", fail):
            self.assertEqual(self.refused(), "link_fetch_failed")
        self.assertEqual(self.google.requests, [])


class SizeAndTimeTests(FetchCase):
    def test_a_declared_size_over_the_limit_is_refused_unread(self) -> None:
        with patch.object(link_module, "MAX_PROTOCOL_PDF_BYTES", 1000):
            self.google.file(drive_download(), b"%PDF-" + b"x" * 10,
                             headers={"content-length": "1001"})
            with self.assertRaises(LinkImportError) as caught:
                self.fetch()
        self.assertEqual((caught.exception.code, caught.exception.limit_bytes),
                         ("link_too_large", 1000))
        self.assertFalse((self.directory / "linked.download").exists())

    def test_a_stream_past_the_limit_is_cut_and_nothing_kept(self) -> None:
        def chunks():
            yield b"%PDF-1.7\n"
            while True:
                yield b"x" * 4096

        self.google.answer(drive_download(), lambda request: httpx.Response(
            200, content=chunks(), headers={"content-type": "application/pdf"}))
        with patch.object(link_module, "MAX_PROTOCOL_PDF_BYTES", 50_000):
            self.assertEqual(self.refused(), "link_too_large")
        self.assertFalse((self.directory / "linked.download").exists())

    def test_the_limit_itself_is_allowed(self) -> None:
        body = pdf_bytes()
        with patch.object(link_module, "MAX_PROTOCOL_PDF_BYTES", len(body)):
            self.google.file(drive_download(), body, content_type="application/pdf")
            self.assertEqual(self.fetch().byte_size, len(body))

    def test_the_limits_are_the_upload_s_own(self) -> None:
        from voiney_lab.experiment_protocol_pdf import MAX_PROTOCOL_PDF_BYTES
        from voiney_lab.protocol_catalog import MAX_REAGENT_TABLE_BYTES

        self.assertIs(link_module.MAX_PROTOCOL_PDF_BYTES, MAX_PROTOCOL_PDF_BYTES)
        self.assertIs(link_module.MAX_REAGENT_TABLE_BYTES, MAX_REAGENT_TABLE_BYTES)

    def test_a_reagent_table_stops_at_the_table_limit(self) -> None:
        with patch.object(link_module, "MAX_REAGENT_TABLE_BYTES", 100):
            self.google.file(
                "https://docs.google.com/spreadsheets/d/1SheetIdForAFictionalReagentTable_abcdefgh"
                "/export?format=xlsx", b"PK" + b"x" * 200)
            with self.assertRaises(LinkImportError) as caught:
                self.fetch("https://docs.google.com/spreadsheets/d/"
                           "1SheetIdForAFictionalReagentTable_abcdefgh/edit", reagent_table=True)
        self.assertEqual((caught.exception.code, caught.exception.limit_bytes),
                         ("link_too_large", 100))

    def test_a_download_past_its_deadline_stops(self) -> None:
        ticks = iter([0.0] + [link_module.DOWNLOAD_DEADLINE_SECONDS + 1.0] * 10)
        self.google.file(drive_download(), pdf_bytes(), content_type="application/pdf")
        self.assertEqual(self.refused(clock=lambda: next(ticks)), "link_timeout")
        self.assertFalse((self.directory / "linked.download").exists())

    def test_a_connection_or_read_timeout(self) -> None:
        for error in (httpx.ConnectTimeout("slow"), httpx.ReadTimeout("slow")):
            with self.subTest(error=type(error).__name__):
                self.google.answer(drive_download(), error)
                self.assertEqual(self.refused(), "link_timeout")
        self.assertEqual(link_module.DOWNLOAD_DEADLINE_SECONDS, 60.0)


class FormatFromBytesTests(FetchCase):
    def test_the_bytes_decide_not_google_s_type_or_the_name(self) -> None:
        cases = (
            (pdf_bytes(), "image/png", "x.png", "application/pdf", "x.png.pdf"),
            (photo_bytes(fmt="PNG"), "application/pdf", "scan.pdf", "image/png", "scan.pdf.png"),
            (photo_bytes(), "text/plain", "bench.JPG", "image/jpeg", "bench.JPG"),
            (docx_bytes(), "application/pdf", "notes.pdf", link_module.DOCX_MEDIA_TYPE,
             "notes.pdf.docx"),
        )
        for body, declared, name, expected, filename in cases:
            with self.subTest(expected=expected):
                self.google.file(drive_download(), body, content_type=declared, filename=name)
                for path in self.directory.iterdir():
                    path.unlink()
                fetched = self.fetch()
                self.assertEqual(fetched.media_type, expected)
                self.assertEqual(fetched.filename, filename)

    def test_a_heic_photo_is_read_by_its_brand(self) -> None:
        body = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
        self.google.file(drive_download(), body, content_type="application/octet-stream")
        self.assertEqual(self.fetch().media_type, "image/heic")

    def test_what_the_upload_does_not_read_is_refused(self) -> None:
        for body in (b"plain text", b"MZ\x90\x00 an executable", b"PK\x03\x04 broken zip",
                     b"GIF89a....", b""):
            with self.subTest(body=body[:12]):
                for path in self.directory.iterdir():
                    path.unlink()
                self.google.file(drive_download(), body, content_type="application/pdf")
                self.assertEqual(self.refused(), "link_unsupported_format")

    def test_a_zip_that_is_neither_word_nor_excel_is_refused(self) -> None:
        import io
        import zipfile

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("readme.txt", "hello")
        self.google.file(drive_download(), out.getvalue())
        self.assertEqual(self.refused(), "link_unsupported_format")

    def test_a_workbook_is_a_reagent_table_and_only_that(self) -> None:
        self.google.file(drive_download(), xlsx_bytes())
        self.assertEqual(self.refused(), "link_sheet_needs_protocol")
        for path in self.directory.iterdir():
            path.unlink()
        self.assertEqual(self.fetch(reagent_table=True).media_type,
                         link_module.REAGENT_TABLE_MEDIA_TYPE)
        for path in self.directory.iterdir():
            path.unlink()
        self.google.file(drive_download(), pdf_bytes(), content_type="application/pdf")
        self.assertEqual(self.refused(reagent_table=True), "link_not_a_reagent_table")

    def test_the_file_name_is_made_plain(self) -> None:
        cases = (
            ('attachment; filename="../../etc/passwd.pdf"', "passwd.pdf"),
            ("attachment; filename*=UTF-8''%EC%8B%9C%EB%A3%8C%20%EC%A0%84%EC%B2%98%EB%A6%AC.pdf",
             "시료 전처리.pdf"),
            ('attachment; filename="..\\\\win\\\\x.pdf"', "x.pdf"),
            ('attachment; filename="a\x07b.pdf"', "ab.pdf"),
            ('attachment; filename="..."', f"google-drive-{FILE_ID[:12]}.pdf"),
            ("inline", f"google-drive-{FILE_ID[:12]}.pdf"),
            ("attachment; filename*=UTF-8''" + "%EA%B0%80" * 300 + ".pdf", f"{'가' * 200}.pdf"),
        )
        for header, expected in cases:
            with self.subTest(header=header[:40]):
                for path in self.directory.iterdir():
                    path.unlink()
                self.google.file(drive_download(), pdf_bytes(), content_type="application/pdf",
                                 headers={"content-disposition": header})
                self.assertEqual(self.fetch().filename, expected)


class PrivateFileTests(FetchCase):
    def test_google_s_sign_in_means_the_file_is_not_shared_publicly(self) -> None:
        answers = (
            httpx.Response(302, headers={
                "location": "https://accounts.google.com/ServiceLogin?service=wise&continue=x"}),
            httpx.Response(302, headers={
                "location": "https://accounts.google.com/v3/signin/identifier?continue=x"}),
            httpx.Response(302, headers={"location": "https://docs.google.com/ServiceLogin?x"}),
            httpx.Response(401),
            httpx.Response(403, content=b"<html>Access denied</html>"),
            httpx.Response(200, content=(
                b"<!DOCTYPE html><html><body><form action="
                b"\"https://accounts.google.com/ServiceLogin\"></form></body></html>"),
                headers={"content-type": "text/html"}),
        )
        for answer in answers:
            with self.subTest(status=answer.status_code):
                for path in self.directory.iterdir():
                    path.unlink()
                self.google.requests.clear()
                self.google.answer(drive_download(), answer)
                self.assertEqual(self.refused(), "link_private")
                # Google's sign-in is never asked.
                self.assertEqual(self.urls(), [drive_download()])

    def test_drive_s_virus_scan_page_is_a_file_too_large(self) -> None:
        self.google.file(drive_download(), (
            b"<!DOCTYPE html><html><head><title>Google Drive - Virus scan warning</title>"
            b"</head><body>Google Drive can't scan this file for viruses.</body></html>"),
            content_type="text/html")
        self.assertEqual(self.refused(), "link_too_large")

    def test_a_file_that_does_not_exist(self) -> None:
        self.assertEqual(self.refused(), "link_not_found")


class RouteSecurityTests(LinkServerCase):
    async def test_a_refused_download_registers_nothing(self) -> None:
        self.google.redirect(drive_download(), "https://evil.example/x.pdf", status=302)
        response = await self.import_link(DRIVE_LINK)
        self.assertEqual(response.status_code, 502, response.text)
        catalog, store = self._open_catalog()
        try:
            self.assertEqual(store.list_experiments(), ())
        finally:
            store.close()

    async def test_the_upload_route_s_own_checks_still_apply(self) -> None:
        # A file whose header reads as a PDF but is not one is refused by the
        # upload route the link hands it to, as an upload would be.
        self.google.file(drive_download(), b"%PDF-1.7\nnot really a pdf",
                         content_type="application/pdf")
        response = await self.import_link(DRIVE_LINK)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"], "invalid_pdf")


if __name__ == "__main__":
    unittest.main()
