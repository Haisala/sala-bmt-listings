"""Static UI contract for one-touch Intake -> private Content flow."""
from __future__ import annotations
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    source=(ROOT/"admin/admin_server.py").read_text(encoding="utf-8")
    home_ui=(ROOT/"admin/one_touch_ui.py").read_text(encoding="utf-8")
    template=re.search(r'def page\(\) -> bytes:\n    return """(.*?)"""\.replace',source,re.S)
    assert template,"ADMIN_PAGE_TEMPLATE_MISSING"
    scripts=re.findall(r"<script>(.*?)</script>",template.group(1),re.S)
    assert scripts,"ADMIN_PAGE_SCRIPT_MISSING"
    script=scripts[-1]
    assert 'id="intake-save" type="submit">TẠO CONTENT' in template.group(1)
    for required in ("/api/intakes/'+encodeURIComponent(intakeId)+'/create-content", "idempotency_key:key",
                     "sessionStorage", "new FormData(intakeForm)", "data.append('media'",
                     "credentials:'same-origin'", "await loadReviews()", "await window.loadReview(d.review_id)",
                     "host.replaceChildren()", "DRAFT_NEEDS_REVIEW", "TIẾP TỤC XỬ LÝ", "XEM 5 CONTENT"):
        assert required in script,f"ONE_TOUCH_UI_MISSING:{required}"
    for required in ("window.openMissingFields", "testOnly(x)", "missing-fields-dialog", "expected_input_revision"):
        assert required in home_ui,f"ONE_TOUCH_V3_UI_MISSING:{required}"
    submit=script.split("intakeForm.addEventListener('submit'",1)[1].split("$('#search').onsubmit",1)[0]
    assert "/process" not in submit and "createContent(pendingSavedIntakeId" in submit
    assert "if(completed){intakeForm.reset()" in submit,"FORM_RESET_BEFORE_CONTENT_RESULT"
    assert "pendingSavedIntakeId?' · Intake đã lưu: '" in submit,"SAVED_INTAKE_RECOVERY_NOT_VISIBLE"
    assert 'id="intake-new"' in template.group(1) and "newIntake.onclick" in script
    assert "TEST_ONLY" in script and "textContent" in script
    for required in ("CONTENT_STUDIO_UI + PUBLIC_RELEASE_UI + ANALYTICS_UI", "BMT_ENV", "Lưu xác nhận nguồn", "CONTENT_PHASE_DISABLED"):
        assert required in source,f"SERVER_ONE_TOUCH_GATE_MISSING:{required}"
    print("PASS: multipart Intake, one-touch route, idempotent replay key, retained form on failure, saved-row retry, TEST_ONLY queue guard")
if __name__=="__main__":main()
