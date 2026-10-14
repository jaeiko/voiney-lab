"""Lane UI3, decision 3 (2026-10-14): the upload takes Word documents and photos.

Lane AN (#79) lets the server take a .docx, a JPG, a PNG or a HEIC photo and
turn it into the PDF it analyses. The page's file input now offers those
types and sends each file with its own type -- by its name's extension when
the browser gives none -- instead of always ``application/pdf``. The new
error codes read in Korean (the server sends a code, no sentence), and a
server without HEIC support (pillow-heif is optional, human decision of
2026-10-13) is answered with the iPhone setting that takes JPEG photos.
"""

from __future__ import annotations

import unittest

from tests.test_screen_cleanup import INDEX, run_page_script

UPLOAD = r"""
const calls=[];
const refused=(code,status=503)=>({ok:false,status,headers:{get:()=>"application/json"},json:async()=>({detail:code})});
function serve(answer){globalThis.fetch=async(url,options={})=>{calls.push({url:String(url),method:options.method||"GET",type:options.headers?.["Content-Type"]||null,status:node("protocol-upload-status").textContent});return answer(String(url),options);};}
async function pick(name,type){calls.length=0;node("protocol-pdf").files=[{name,type}];await registerSelectedProtocol();}
const status=()=>node("protocol-upload-status").textContent;
"""
HEIC_STEPS = "아이폰 설정 → 카메라 → 포맷 → 높은 호환성"


class InputTests(unittest.TestCase):

    def test_the_input_offers_word_and_photos_under_its_own_id(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        tag = html[html.index('<input id="protocol-pdf"'):]
        tag = tag[:tag.index(">") + 1]
        accept = tag.split('accept="', 1)[1].split('"', 1)[0].split(",")
        for kind in (".pdf", "application/pdf", ".docx", ".jpg", ".jpeg", ".png", ".heic",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     "image/jpeg", "image/png", "image/heic"):
            self.assertIn(kind, accept)
        self.assertIn('aria-describedby="protocol-upload-status"', tag)
        self.assertIn('<label id="protocol-pdf-pick" for="protocol-pdf" class="btn-file-select" role="button" tabindex="0">원문 파일 선택</label>', html)
        self.assertIn('<small id="protocol-heic-hint" class="protocol-heic-hint" role="note" hidden></small>', html)


class ContentTypeTests(unittest.TestCase):

    def test_each_file_goes_with_its_own_type_or_its_extension(self) -> None:
        result = run_page_script(UPLOAD + r"""
serve(()=>refused("protocol_catalog_unavailable"));
const cases=[
 ["protocol.docx","application/vnd.openxmlformats-officedocument.wordprocessingml.document","application/vnd.openxmlformats-officedocument.wordprocessingml.document","Word 문서를 PDF로 바꿔 등록하는 중…"],
 ["bench.JPG","image/jpeg","image/jpeg","사진을 PDF로 바꿔 등록하는 중…"],
 ["gel.png","image/png","image/png","사진을 PDF로 바꿔 등록하는 중…"],
 ["IMG_0412.HEIC","","image/heic","사진을 PDF로 바꿔 등록하는 중…"],
 ["notes.docx","","application/vnd.openxmlformats-officedocument.wordprocessingml.document","Word 문서를 PDF로 바꿔 등록하는 중…"],
 ["scan.pdf","","application/pdf","원문 PDF를 검증하고 등록하는 중…"],
 ["selected.pdf","application/pdf","application/pdf","원문 PDF를 검증하고 등록하는 중…"],
];
for(const [name,type,sent,line] of cases){
 await pick(name,type);
 assert(calls.length===1&&calls[0].method==="POST"&&calls[0].url===`/api/protocols?filename=${encodeURIComponent(name)}`,`${name}: ${JSON.stringify(calls)}`);
 assert(calls[0].type===sent,`${name} went as ${calls[0].type}`);
 assert(calls[0].status===line,`${name} while it uploads: ${calls[0].status}`);
}
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_file_the_server_does_not_take_is_not_sent(self) -> None:
        result = run_page_script(UPLOAD + r"""
serve(()=>{throw new Error("sent")});
for(const [name,type] of [["notes.txt","text/plain"],["sheet.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],["photo.webp","image/webp"],["noname",""]]){
 await pick(name,type);
 assert(calls.length===0,`${name} was sent`);
 assert(status()==="PDF, Word(.docx), 사진(JPG·PNG·HEIC)만 등록할 수 있습니다.",`${name}: ${status()}`);
 assert(node("protocol-pdf").value==="",`${name} stayed selected`);
}
node("protocol-pdf").files=[];await registerSelectedProtocol();
assert(status()==="등록할 원문 파일(PDF, Word, 사진)을 선택해 주세요.",status());
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class ErrorWordTests(unittest.TestCase):

    def test_the_new_codes_read_in_korean(self) -> None:
        result = run_page_script(UPLOAD + r"""
node("protocol-heic-hint").hidden=true;  // as the markup has it; the fake DOM starts shown
const unexpected=protocolUploadErrorMessage("unexpected_server_error");
const cases=[
 ["docx_conversion_unavailable",503,"Word(.docx)"],
 ["heic_support_unavailable",503,"아이폰 설정 → 카메라 → 포맷 → 높은 호환성"],
 ["image_support_unavailable",503,"사진"],
 ["source_conversion_unavailable",503,"PDF로 바꾸지"],
 ["invalid_source_file",422,"읽을 수 없습니다"],
 ["protocol_image_too_large",413,"화소"],
 ["source_conversion_failed",422,"Word 문서를 PDF로 바꾸지 못했습니다"],
 ["unsupported_pdf_media_type",415,"PDF, Word(.docx), 사진(JPG·PNG·HEIC)만"],
];
for(const [code,http,words] of cases){
 serve(()=>refused(code,http));
 await pick(code==="heic_support_unavailable"?"IMG_1.heic":code.includes("docx")||code==="source_conversion_failed"?"a.docx":"a.jpg",code==="heic_support_unavailable"?"image/heic":code.includes("docx")||code==="source_conversion_failed"?"application/vnd.openxmlformats-officedocument.wordprocessingml.document":"image/jpeg");
 assert(status()!==unexpected&&status().includes(words),`${code}: ${status()}`);
 assert(!/[a-z]+_[a-z_]+/.test(status()),`${code} shows a raw code: ${status()}`);
}
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_a_server_without_heic_names_the_iphone_setting(self) -> None:
        result = run_page_script(UPLOAD + r"""
const hint=node("protocol-heic-hint");hint.hidden=true;
serve(()=>refused("heic_support_unavailable"));
await pick("IMG_0412.HEIC","image/heic");
assert(status().includes("아이폰 설정 → 카메라 → 포맷 → 높은 호환성"),status());
assert(hint.hidden===true,"the refusal repeated the guidance in a second line");
serve(url=>{assert(url==="/readyz",`asked ${url}`);return {ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>({status:"ok",capabilities:{docx_conversion_available:true,heic_photos_available:true}})};});
assert(await loadSourceFormats()===true&&hint.hidden===true,"a server with HEIC kept the hint");
serve(()=>({ok:true,status:200,headers:{get:()=>"application/json"},json:async()=>({status:"ok",capabilities:{docx_conversion_available:true,heic_photos_available:false}})}));
assert(await loadSourceFormats()===false&&hint.hidden===false,"a server without HEIC did not say so before the upload");
serve(()=>refused("heic_support_unavailable"));
await pick("IMG_0413.HEIC","image/heic");
assert(status()==="이 서버는 HEIC 사진을 읽지 못합니다 · 아래 안내대로 JPG 사진을 올려 주세요."&&hint.hidden===false,`refused beside the hint: ${status()}`);
serve(()=>({ok:false,status:503,headers:{get:()=>"application/json"},json:async()=>({status:"not_ready",reason:"ValueError"})}));
assert(await loadSourceFormats()===null&&hint.hidden===true,"a hint was drawn from no answer");
serve(()=>{throw new TypeError("offline")});
assert(await loadSourceFormats()===null&&hint.hidden===true,"a hint was drawn from no connection");
""")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_page_asks_for_the_formats_once_it_loads(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        load = html[html.index('globalThis.addEventListener?.("load",'):]
        load = load[:load.index("{once:true});")]
        self.assertIn("void loadSourceFormats();", load)


if __name__ == "__main__":
    unittest.main()
