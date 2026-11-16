from conftest import load_script

s2m = load_script("storage2md")


def test_panel_body_is_not_duplicated_by_fallback():
    storage = (
        '<ac:adf-extension><ac:adf-node type="panel"><ac:adf-attribute key="panel-type">note</ac:adf-attribute>'
        '<ac:adf-content><p><strong>권고</strong>: 정산은 하루 한 번 확정한다.</p></ac:adf-content></ac:adf-node>'
        '<ac:adf-fallback><div class="panel"><p><strong>권고</strong>: 정산은 하루 한 번 확정한다.</p></div>'
        "</ac:adf-fallback></ac:adf-extension>"
    )
    md = s2m.convert(storage)
    assert md.count("정산은 하루 한 번 확정한다") == 1
    assert md.startswith("> **권고**")
    assert "note" not in md


def test_jira_macro_keeps_linked_ticket_key():
    storage = ('<p>티켓 <ac:structured-macro ac:name="jira"><ac:parameter ac:name="key">PAY-4553</ac:parameter>'
               "</ac:structured-macro> 참고</p>")
    assert "[PAY-4553](https://jira.invalid/browse/PAY-4553)" in s2m.convert(storage)
    assert "[PAY-4553](https://x.example/browse/PAY-4553)" in s2m.convert(storage, "https://x.example/browse/")


def test_task_list_status_text_is_dropped():
    storage = (
        "<ac:task-list><ac:task><ac:task-id>1</ac:task-id><ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>재시도 한도를 정한다</ac:task-body></ac:task>"
        "<ac:task><ac:task-id>2</ac:task-id><ac:task-status>complete</ac:task-status>"
        "<ac:task-body>알림을 붙인다</ac:task-body></ac:task></ac:task-list>"
    )
    md = s2m.convert(storage)
    assert "- 재시도 한도를 정한다" in md and "- 알림을 붙인다" in md
    assert "complete" not in md and "1재시도" not in md


def test_table_list_heading_and_code():
    storage = (
        "<h2>흐름</h2><ol><li><p>읽기</p></li><li><p>계산</p></li></ol>"
        "<table><tbody><tr><th><p>단계</p></th><th><p>시각</p></th></tr>"
        "<tr><td><p>확정</p></td><td><p>02:00</p></td></tr></tbody></table>"
        '<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">bash</ac:parameter>'
        "<ac:plain-text-body><![CDATA[echo <ok>]]></ac:plain-text-body></ac:structured-macro>"
    )
    md = s2m.convert(storage)
    assert "## 흐름" in md and "1. 읽기" in md and "2. 계산" in md
    assert "| 단계 | 시각 |" in md and "| 확정 | 02:00 |" in md
    assert "```bash\necho <ok>\n```" in md
