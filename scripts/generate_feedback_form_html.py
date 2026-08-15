#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: generate_feedback_form_html.py
목적: 영양사 **피드백 수집용** self-contained HTML 폼을 생성한다.

`generate_menu_review_html.py`(읽기 전용 검수 자료)와 역할이 다르다:
    검수 자료 = "이렇게 나왔습니다" 를 보여 준다.
    이 폼    = "그래서 어떻게 할까요" 의 **답을 회수한다.**

왜 회수가 핵심인가:
    어울림 점수(`data/processed/menu_affinity.csv`)와 급식 대상 기준
    (`data/processed/user_group_profiles.csv`)을 코드가 아니라 **데이터**로 둔 이유가
    "검수 결과로 덮어쓸 수 있어야 한다"였다. 답이 돌아오지 않으면 그 설계가 무의미하다.
    그래서 이 폼은 **입력값을 JSON 으로 내보낸다** — 그 JSON 이 위 두 파일과
    DB 재분류로 직행하도록 키를 맞춰 두었다.

백엔드가 없다(자료를 이메일·USB 로 주고받는 현장 조건). 그래서:
    · 입력은 `localStorage` 에 자동 저장 — 창을 닫아도 이어서 작성한다.
    · 제출은 **JSON 파일 다운로드** + 클립보드 복사. 인쇄용 레이아웃도 둔다.
    · 외부 CDN·폰트·스크립트를 쓰지 않는다(오프라인 개봉 가능).

실행:
    docker exec optimeal_app python scripts/generate_feedback_form_html.py
    docker exec optimeal_app python scripts/generate_feedback_form_html.py \
        --profiles middle_mix,senior_mix --days 3
"""

import argparse
import html
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "module_3" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import csp_hard_constraints as hc  # noqa: E402
import csp_solver as cs  # noqa: E402
import menu_affinity as ma  # noqa: E402
import soft_constraints_diversity as scd  # noqa: E402
import user_profiles as up  # noqa: E402

FORM_VERSION = "1.0"
DEFAULT_PROFILE_KEYS = "middle_mix,senior_mix"
OUT_DIR = REPO_ROOT / "reports"


def esc(s) -> str:
    """HTML 이스케이프."""
    return html.escape(str(s))


def solve(menus, profile, days, time_limit, sodium_by_idx, main_by_idx, affinity_table):
    """프로파일 1종의 식단을 푼다. 기준값은 프로파일 + 끼니 수에서 나온다."""
    meals = {1: ("점심",), 2: ("점심", "저녁")}.get(
        profile.default_meals, ("아침", "점심", "저녁"))
    tg = up.targets_for(profile, meals)
    cfg = hc.HardConstraintConfig(
        target_kcal_per_day=tg["target_kcal_per_day"],
        budget_limit_per_person=None,           # 원가 0원이라 예산 제약은 무의미
        nutrient_max_per_day={"sodium": tg["sodium_max_mg_per_day"]},
        enable_staple_main=True, enable_menu_pairing=True)
    if tg["meal_energy_ratios"]:
        cfg.meal_energy_ratios = tg["meal_energy_ratios"]
    res = cs.build_and_solve(menus, cs.MealPlanRequest(
        days=days, meals=meals, hard=cfg, solver_time_limit=time_limit,
        hard_nutrient_by_idx={"sodium": sodium_by_idx},
        main_by_idx=main_by_idx, affinity_table=affinity_table))
    return res, meals, tg


# ===========================================================================
# 섹션 렌더링
# ===========================================================================
def render_meal_cards(sections_data) -> str:
    """끼니별 평가 카드. 끼니마다 (판정 · 안 어울리는 조합 · 메모)를 받는다.

    **메뉴를 체크박스로 둔 이유**: "무엇이 안 어울리는지"를 자유 서술로 받으면 표기가
    제각각이라 근거표로 옮길 수 없다. 실제 편성된 메뉴명을 그대로 고르게 해야
    `menu_affinity.csv` 의 유형쌍으로 환원할 수 있다.
    """
    out = []
    for sec in sections_data:
        pname, meals_names, days_plan = sec["profile_name"], sec["meals"], sec["plan"]
        out.append(f"<h3>{esc(pname)} · {esc('/'.join(meals_names))} "
                   f"({esc(sec['kcal'])}kcal · 나트륨 ≤{esc(sec['sodium'])}mg)</h3>")
        out.append(f"<p class='src'>기준 근거: {esc(sec['basis'])}</p>")
        for day, meals in days_plan.items():
            for mname, picks in meals.items():
                mid = f"{sec['key']}-{day}-{mname}"
                chips = "".join(
                    f"<label class='chip'><input type='checkbox' data-kind='badpair' "
                    f"data-meal='{esc(mid)}' value='{esc(n)}'>{esc(n)}"
                    f"<span class='cat'>{esc(cat)}</span></label>"
                    for n, cat in picks)
                out.append(f"""
<div class="card" data-meal="{esc(mid)}">
  <div class="cardhead"><b>{day}일차 {esc(mname)}</b>
    <span class="kc">{sec['daily_kcal'].get(day, 0):.0f}kcal/일</span></div>
  <div class="dishes">{chips}</div>
  <div class="ask">
    <span class="q">이 끼니, 현장에서 이대로 낼 수 있습니까?</span>
    <label><input type="radio" name="v-{esc(mid)}" data-kind="verdict"
      data-meal="{esc(mid)}" value="ok"> 가능</label>
    <label><input type="radio" name="v-{esc(mid)}" data-kind="verdict"
      data-meal="{esc(mid)}" value="fix"> 수정하면 가능</label>
    <label><input type="radio" name="v-{esc(mid)}" data-kind="verdict"
      data-meal="{esc(mid)}" value="reject"> 부적합</label>
  </div>
  <div class="ask small">위 메뉴 중 <b>서로 어울리지 않는 것</b>에 체크해 주세요
    (체크한 것들이 한 끼에 같이 나오면 안 된다는 뜻으로 받습니다).</div>
  <textarea rows="2" data-kind="note" data-meal="{esc(mid)}"
    placeholder="수정이 필요하다면 무엇을 어떻게 (예: 국이 너무 짜다 / 조리 동선이 겹친다)"></textarea>
</div>""")
    return "".join(out)


def render_category_check(sample) -> str:
    """주찬/부찬/김치 분류 확인. 메뉴명 키워드 분류라 경계가 현장 관행과 다를 수 있다."""
    rows = "".join(f"""
<tr><td>{esc(name)}</td><td><span class="tag">{esc(cat)}</span></td>
<td>
  <label><input type="radio" name="c-{i}" data-kind="cat" data-menu="{esc(name)}"
    data-current="{esc(cat)}" value="ok" checked> 맞음</label>
  <label><input type="radio" name="c-{i}" data-kind="cat" data-menu="{esc(name)}"
    data-current="{esc(cat)}" value="주찬"> 주찬</label>
  <label><input type="radio" name="c-{i}" data-kind="cat" data-menu="{esc(name)}"
    data-current="{esc(cat)}" value="부찬"> 부찬</label>
  <label><input type="radio" name="c-{i}" data-kind="cat" data-menu="{esc(name)}"
    data-current="{esc(cat)}" value="김치"> 김치</label>
</td></tr>""" for i, (name, cat) in enumerate(sample))
    return f"""<table class="chk"><thead><tr><th>메뉴</th><th>현재 분류</th>
<th>맞습니까? 아니면 무엇입니까</th></tr></thead><tbody>{rows}</tbody></table>"""


def render_method_table(coefs) -> str:
    """지금 감점 중인 조리법 중복과 그 강도. '이 판단이 맞는가'를 묻는다."""
    rows = "".join(
        f"<tr><td>{esc(g)}</td><td class='num'>{lift:.2f}</td>"
        f"<td class='num'>{coef}</td>"
        f"<td><label><input type='radio' name='m-{esc(g)}' data-kind='method' "
        f"data-method='{esc(g)}' value='keep' checked> 적절</label>"
        f"<label><input type='radio' name='m-{esc(g)}' data-kind='method' "
        f"data-method='{esc(g)}' value='stronger'> 더 강하게</label>"
        f"<label><input type='radio' name='m-{esc(g)}' data-kind='method' "
        f"data-method='{esc(g)}' value='weaker'> 더 약하게</label>"
        f"<label><input type='radio' name='m-{esc(g)}' data-kind='method' "
        f"data-method='{esc(g)}' value='off'> 상관없음</label></td></tr>"
        for g, lift, coef in coefs)
    return f"""<table class="chk"><thead><tr><th>조리법</th>
<th>실제 급식에서<br>같이 나오는 정도</th><th>현재 감점</th>
<th>한 끼에 2접시 이상이면?</th></tr></thead><tbody>{rows}</tbody></table>"""


RUBRIC_ITEMS = (("taste", "① 맛·간 적절성"),
                ("feasibility", "② 조리 실현 가능성"),
                ("field", "③ 대량 조리 현장 적합성"))


def render_rubric_rows() -> str:
    """ADR-002 루브릭 3항목 × 5점 라디오."""
    rows = []
    for key, label in RUBRIC_ITEMS:
        cells = "".join(
            f"<td class='sc'><input type='radio' name='rb-{key}' data-kind='rubric' "
            f"data-item='{key}' value='{v}'></td>" for v in range(1, 6))
        rows.append(f"<tr><td>{esc(label)}</td>{cells}</tr>")
    return "".join(rows)


CSS = """
:root{--fg:#1a1a1a;--mut:#666;--line:#d9d9d9;--ok:#0a7d3c;--bad:#c0392b;
--accent:#1c5fa8;--bg:#fff;--card:#fafafa;--warn:#fff8e1}
*{box-sizing:border-box}
body{margin:0;padding:28px 18px 80px;font-family:-apple-system,'Apple SD Gothic Neo',
'Malgun Gothic',sans-serif;color:var(--fg);background:var(--bg);line-height:1.65}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:1.55rem;margin:0 0 6px}
h2{font-size:1.15rem;margin:34px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--fg)}
h3{font-size:1rem;margin:22px 0 8px;color:var(--accent)}
.meta{color:var(--mut);font-size:.85rem;margin-bottom:18px}
.box{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:14px 16px;margin:12px 0}
.box.warn{background:var(--warn);border-color:#e6c65c}
.src{color:var(--mut);font-size:.82rem;margin:2px 0 10px}
.card{border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0;
background:var(--card)}
.cardhead{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
.kc{color:var(--mut);font-size:.82rem}
.dishes{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);
border-radius:999px;padding:5px 11px;background:#fff;font-size:.9rem;cursor:pointer}
.chip:has(input:checked){border-color:var(--bad);background:#fdecea;color:var(--bad);
font-weight:600}
.chip .cat{color:var(--mut);font-size:.75rem}
.chip:has(input:checked) .cat{color:var(--bad)}
.ask{margin:8px 0;display:flex;flex-wrap:wrap;gap:14px;align-items:center}
.ask.small{font-size:.85rem;color:var(--mut);display:block}
.ask .q{font-weight:600}
label{cursor:pointer}
textarea,input[type=text]{width:100%;border:1px solid var(--line);border-radius:6px;
padding:7px 9px;font:inherit;font-size:.9rem}
table{border-collapse:collapse;width:100%;font-size:.9rem;margin:8px 0}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
th{background:#f2f4f7}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.chk td label{margin-right:12px;white-space:nowrap}
.tag{background:#eef2f7;border-radius:4px;padding:1px 6px;font-size:.8rem}
.q-block{border-left:4px solid var(--accent);padding:10px 14px;margin:14px 0;
background:var(--card)}
.q-block .opts{margin-top:8px;display:flex;flex-direction:column;gap:6px}
.evi{color:var(--mut);font-size:.85rem;margin-top:6px}
.rubric td.sc{text-align:center}
.bar{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);
padding:10px 18px;display:flex;gap:10px;align-items:center;justify-content:center;
box-shadow:0 -2px 10px rgba(0,0,0,.06)}
button{font:inherit;font-size:.92rem;padding:9px 16px;border-radius:7px;
border:1px solid var(--accent);
background:var(--accent);color:#fff;cursor:pointer}
button.ghost{background:#fff;color:var(--accent)}
#status{color:var(--mut);font-size:.85rem}
.addrow{margin-top:8px}
@media print{
  .bar{display:none}
  body{padding:0}
  .card{break-inside:avoid}
}
"""

JS = """
(function(){
  var KEY = 'optimeal-feedback-' + document.body.dataset.formId;
  var $ = function(s,r){return (r||document).querySelectorAll(s);};

  function collect(){
    var d = JSON.parse(document.getElementById('form-meta').textContent);
    d.submitted_at = new Date().toISOString();
    d.reviewer = {};
    $('[data-reviewer]').forEach(function(el){ d.reviewer[el.dataset.reviewer] = el.value; });
    d.decisions = {};
    $('input[data-kind=decision]:checked').forEach(function(el){
      d.decisions[el.dataset.q] = el.value; });
    $('[data-kind=decision-note]').forEach(function(el){
      if (el.value.trim()) d.decisions[el.dataset.q + '_note'] = el.value.trim(); });
    var meals = {};
    function slot(id){ if(!meals[id]) meals[id]={meal:id,verdict:null,bad_pair:[],note:''};
      return meals[id]; }
    $('input[data-kind=verdict]:checked').forEach(function(el){
      slot(el.dataset.meal).verdict = el.value; });
    $('input[data-kind=badpair]:checked').forEach(function(el){
      slot(el.dataset.meal).bad_pair.push(el.value); });
    $('[data-kind=note]').forEach(function(el){
      if (el.value.trim()) slot(el.dataset.meal).note = el.value.trim(); });
    d.meal_reviews = Object.keys(meals).map(function(k){ return meals[k]; })
      .filter(function(m){ return m.verdict || m.bad_pair.length || m.note; });
    d.category_fixes = [];
    $('input[data-kind=cat]:checked').forEach(function(el){
      if (el.value !== 'ok')
        d.category_fixes.push({menu: el.dataset.menu, current: el.dataset.current,
                               correct: el.value}); });
    d.method_feedback = {};
    $('input[data-kind=method]:checked').forEach(function(el){
      d.method_feedback[el.dataset.method] = el.value; });
    d.forbidden_pairs = [];
    $('.pairrow').forEach(function(row){
      var a = row.querySelector('[data-pair=a]').value.trim();
      var b = row.querySelector('[data-pair=b]').value.trim();
      var why = row.querySelector('[data-pair=why]').value.trim();
      if (a && b) d.forbidden_pairs.push({a:a, b:b, reason:why}); });
    d.rubric = {};
    $('input[data-kind=rubric]:checked').forEach(function(el){
      d.rubric[el.dataset.item] = Number(el.value); });
    var rc = document.getElementById('rubric-comment');
    if (rc && rc.value.trim()) d.rubric.comment = rc.value.trim();
    return d;
  }

  function save(){
    try { localStorage.setItem(KEY, JSON.stringify(snapshot())); } catch(e){}
    var n = collect().meal_reviews.length;
    document.getElementById('status').textContent =
      '자동 저장됨 · 평가한 끼니 ' + n + '건';
  }

  // 저장/복원은 **입력 요소 단위**로 한다. collect() 결과를 저장하면
  // 되돌릴 때 어느 입력이었는지 알 수 없다.
  // 키는 **안정적인 것**만 쓴다(name·data-*). DOM 순서 인덱스로 키를 잡으면
  // 동적으로 늘어나는 금기 조합 행에서 값이 엉뚱한 칸으로 복원된다.
  function textKey(el){
    if (el.id) return 'id:' + el.id;
    if (el.dataset.kind === 'note') return 'note:' + el.dataset.meal;
    return null;                       // 키를 못 만드는 입력은 저장하지 않는다
  }
  function snapshot(){
    var s = {r:{}, c:{}, t:{}, p:[]};
    $('input[type=radio]:checked').forEach(function(el){ s.r[el.name] = el.value; });
    $('input[type=checkbox]:checked').forEach(function(el){
      s.c[(el.dataset.meal||'') + '|' + el.value] = 1; });
    $('textarea, input[type=text]').forEach(function(el){
      var k = textKey(el); if (k && el.value) s.t[k] = el.value; });
    // 금기 조합은 행 단위 배열로 따로 저장한다(행 수가 가변이라 키가 흔들린다).
    $('.pairrow').forEach(function(row){
      s.p.push([row.querySelector('[data-pair=a]').value,
                row.querySelector('[data-pair=b]').value,
                row.querySelector('[data-pair=why]').value]); });
    return s;
  }
  function restore(){
    var raw; try { raw = localStorage.getItem(KEY); } catch(e){ return; }
    if (!raw) return;
    var s; try { s = JSON.parse(raw); } catch(e){ return; }
    Object.keys(s.r||{}).forEach(function(name){
      var el = document.querySelector('input[name="'+CSS.escape(name)+'"][value="'+
        CSS.escape(s.r[name])+'"]'); if (el) el.checked = true; });
    $('input[type=checkbox]').forEach(function(el){
      if ((s.c||{})[(el.dataset.meal||'') + '|' + el.value]) el.checked = true; });
    $('textarea, input[type=text]').forEach(function(el){
      var k = textKey(el); if (k && (s.t||{})[k]) el.value = s.t[k]; });
    // 저장된 행 수만큼 만들고 채운다. 빈 행이라도 최소 2줄은 남긴다.
    (s.p || []).forEach(function(vals){
      var row = addPair();
      row.querySelector('[data-pair=a]').value = vals[0] || '';
      row.querySelector('[data-pair=b]').value = vals[1] || '';
      row.querySelector('[data-pair=why]').value = vals[2] || ''; });
  }

  function download(){
    var data = collect();
    var name = (data.reviewer.name || 'anonymous').replace(/[^\\w가-힣]/g,'');
    var blob = new Blob([JSON.stringify(data, null, 2)], {type:'application/json'});
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'optimeal_feedback_' + data.generated + '_' + name + '.json';
    a.click(); URL.revokeObjectURL(a.href);
  }

  function copy(){
    var t = JSON.stringify(collect(), null, 2);
    navigator.clipboard.writeText(t).then(function(){
      document.getElementById('status').textContent = '클립보드에 복사했습니다';
    }, function(){
      document.getElementById('status').textContent = '복사 실패 — 파일 저장을 이용하세요';
    });
  }

  function addPair(){
    var tb = document.getElementById('pairs');
    var tr = document.createElement('tr');
    tr.className = 'pairrow';
    tr.innerHTML = '<td><input type="text" data-pair="a" placeholder="예: 국수"></td>' +
      '<td><input type="text" data-pair="b" placeholder="예: 부대찌개"></td>' +
      '<td><input type="text" data-pair="why" placeholder="왜 안 되는지 (선택)"></td>';
    tb.appendChild(tr);
    return tr;   // 복원이 방금 만든 행을 채울 수 있어야 한다
  }

  document.addEventListener('DOMContentLoaded', function(){
    restore();
    // 복원된 행이 2줄에 못 미치면 빈 줄을 채워 준다(처음 여는 경우 포함).
    while (document.querySelectorAll('.pairrow').length < 2) addPair();
    document.addEventListener('change', save);
    document.addEventListener('input', save);
    document.getElementById('btn-save').addEventListener('click', download);
    document.getElementById('btn-copy').addEventListener('click', copy);
    document.getElementById('btn-print').addEventListener('click', function(){ window.print(); });
    document.getElementById('btn-add-pair').addEventListener('click', function(){ addPair(); });
    document.getElementById('btn-reset').addEventListener('click', function(){
      if (!confirm('작성한 내용을 모두 지웁니다. 계속할까요?')) return;
      try { localStorage.removeItem(KEY); } catch(e){}
      location.reload(); });
    save();
  });
})();
"""


def build_html(meta, sections_data, cat_sample, method_coefs, profiles_used) -> str:
    """전체 폼 문서를 조립한다."""
    today = meta["generated"]
    kimchi_rates = "밥 91.8% · 죽 99.0% · 면 71.5% · <b>빵 47.5%</b>"
    na_rows = "".join(
        f"<tr><td>{esc(p.group_name)}</td><td class='num'>{p.daily_kcal:,.0f}</td>"
        f"<td class='num'>{(p.sodium_cdrr_mg or 0):,.0f}</td></tr>"
        for p in profiles_used)
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OptiMeal 영양사 피드백 — {esc(today)}</title>
<style>{CSS}</style></head>
<body data-form-id="{esc(today)}"><div class="wrap">

<h1>OptiMeal 영양사 피드백</h1>
<div class="meta">생성일 {esc(today)} · 폼 버전 {FORM_VERSION} ·
후보 메뉴 {meta['n_menus']}종 · 평가 대상 {meta['n_meals']}끼니</div>

<div class="box"><b>부탁드리는 것</b><br>
시스템이 자동으로 짠 식단입니다. <b>영양 계산과 제약 준수는 기계가 이미 확인</b>했습니다
(열량·나트륨·중복·구성). 저희가 판단할 수 없어 여쭙는 것은 <b>맛·조리 현실성·메뉴 궁합</b>
처럼 현장 경험이 있어야 아는 부분입니다.<br><br>
<b>작성한 내용은 자동 저장</b>되니 중간에 닫으셔도 됩니다. 다 쓰신 뒤 아래
<b>[답변 파일 저장]</b>을 누르면 파일 하나가 만들어집니다. 그 파일만 보내 주시면 됩니다.
</div>

<div class="box">
<b>작성자</b><br>
<table><tr>
<td>성함 <input type="text" data-reviewer="name" id="rv-name"></td>
<td>소속(업장) <input type="text" data-reviewer="site" id="rv-site"></td>
<td>경력(년) <input type="text" data-reviewer="years" id="rv-years"></td>
</tr></table>
</div>

<h2>1. 결정을 부탁드리는 것 (4건)</h2>
<p>아래는 <b>저희가 임의로 정할 수 없는</b> 사항입니다. 데이터가 가리키는 방향을 함께
적었으니, 현장 기준으로 정해 주십시오.</p>

<div class="q-block">
<b>Q1. 빵·면 끼니에도 김치를 넣어야 합니까?</b>
<div class="opts">
<label><input type="radio" name="q1" data-kind="decision" data-q="kimchi_by_staple"
  value="keep_all"> (a) 지금처럼 <b>모든 끼니에 김치 1개</b>를 넣는다</label>
<label><input type="radio" name="q1" data-kind="decision" data-q="kimchi_by_staple"
  value="conditional"> (b) <b>밥·죽만 필수</b>, 빵·면은 넣어도 되고 안 넣어도 된다</label>
</div>
<div class="evi">실제 학교 급식 15,250끼니에서 김치 편성률: {kimchi_rates}.
자료는 (b)를 가리키지만, 이전에 "김치 1개"로 확정해 주신 사항이라 임의로 바꾸지
않았습니다. 지금은 클럽 샌드위치에도 배물김치가 붙습니다.</div>
</div>

<div class="q-block">
<b>Q2. 나트륨 상한을 이 값으로 두어도 됩니까?</b>
<table><thead><tr><th>대상</th><th>1일 열량(kcal)</th><th>1일 나트륨 상한(mg)</th></tr>
</thead><tbody>{na_rows}</tbody></table>
<div class="opts">
<label><input type="radio" name="q2" data-kind="decision" data-q="sodium_limit"
  value="as_is"> 이대로 좋다</label>
<label><input type="radio" name="q2" data-kind="decision" data-q="sodium_limit"
  value="lower"> 더 낮춰야 한다</label>
<label><input type="radio" name="q2" data-kind="decision" data-q="sodium_limit"
  value="higher"> 이 값으로는 맛이 안 난다 (올려야 한다)</label>
</div>
<input type="text" data-kind="decision-note" data-q="sodium_limit" id="q2note"
  placeholder="바꿔야 한다면 몇 mg 이 적당한지, 그렇게 보시는 이유">
<div class="evi">2025 한국인 영양소 섭취기준의 만성질환위험감소섭취량(CDRR)입니다.
1식만 급식하는 경우 이 값의 40%를 상한으로 씁니다.
<b>맛·간이 급식으로 성립하는 하한선</b>을 같이 봐 주시면 좋겠습니다.</div>
</div>

<div class="q-block">
<b>Q3. 한 끼에 같은 조리법이 겹치는 것을 어느 정도로 피해야 합니까?</b>
{render_method_table(method_coefs)}
<div class="evi">"실제 급식에서 같이 나오는 정도"는 학교 급식 자료에서 센 값입니다
(1보다 작으면 현장에서 피하는 경향). 현재 이 정도로 감점하고 있는데, 강도가 맞는지
봐 주십시오. <b>끓이기·부침은 자료가 적어 아직 막지 않습니다.</b></div>
</div>

<div class="q-block">
<b>Q4. 반찬 개수(주찬 1 · 부찬 2~3)가 맞습니까?</b>
<div class="opts">
<label><input type="radio" name="q4" data-kind="decision" data-q="composition"
  value="ok"> 적절하다</label>
<label><input type="radio" name="q4" data-kind="decision" data-q="composition"
  value="change"> 바꿔야 한다</label>
</div>
<input type="text" data-kind="decision-note" data-q="composition" id="q4note"
  placeholder="바꾼다면 어떻게 (예: 부찬 2개 고정 / 주찬 2개 필요)">
</div>

<h2>2. 끼니별로 봐 주십시오</h2>
<p>각 끼니가 <b>현장에서 그대로 낼 수 있는지</b>와, 그 안에 <b>서로 안 어울리는 메뉴</b>가
있는지 표시해 주십시오. 체크하신 조합은 그대로 금지 규칙 후보가 됩니다.</p>
{render_meal_cards(sections_data)}

<h2>3. 메뉴 분류가 맞습니까</h2>
<p>메뉴 이름만 보고 자동 분류한 것이라 <b>주찬/부찬 경계</b>가 현장 감각과 다를 수 있습니다.
아래는 위 식단에 실제로 나온 메뉴입니다.</p>
{render_category_check(cat_sample)}

<h2>4. 같이 내면 안 되는 조합</h2>
<p>위 식단에 없더라도, 현장에서 <b>같이 내지 않는 조합</b>이 있으면 적어 주십시오.
지금 시스템이 아는 금기는 "찌개·전골·탕은 밥과만" 하나뿐입니다.</p>
<table class="chk"><thead><tr><th style="width:30%">이것과</th>
<th style="width:30%">이것은 같이 안 된다</th><th>이유(선택)</th></tr></thead>
<tbody id="pairs"></tbody></table>
<div class="addrow"><button type="button" class="ghost" id="btn-add-pair">+ 줄 추가</button></div>

<h2>5. 종합 평가</h2>
<table class="rubric"><thead><tr><th>항목</th>
<th>1<br>매우 미흡</th><th>2</th><th>3</th><th>4</th><th>5<br>매우 우수</th></tr></thead>
<tbody>
{render_rubric_rows()}
</tbody></table>
<textarea id="rubric-comment" rows="4"
  placeholder="전체적으로 느끼신 점, 이 시스템이 현장에서 쓰이려면 무엇이 더 필요한지"></textarea>
<p><small>3항목 평균 4.00 이상이면 "상용 가능"으로 판정합니다(ADR-002).</small></p>

<div class="box warn"><b>지금 이 식단에 빠져 있는 것</b> — 검수 시 감안해 주십시오.<br>
① <b>식단가(원가)</b>가 없습니다(식재료 단가 미확보). ② <b>알레르기 대체식</b>이 없습니다
(알레르겐 매핑 미구축 — 불완전한 매핑으로 "대응됨"처럼 보이게 하지 않았습니다).
③ 당류·칼륨·인 상한은 <b>메뉴별 함량 자료가 없어</b> 걸지 못했습니다.
④ 맛 계열(단맛·매운맛) 충돌은 자료로 가려낼 수 없어 <b>규칙이 없습니다</b> — 4번 항목에
적어 주시면 그대로 반영합니다.</div>

<script type="application/json" id="form-meta">{json.dumps(meta, ensure_ascii=False)}</script>
</div>
<div class="bar">
  <span id="status">자동 저장 대기</span>
  <button type="button" id="btn-save">답변 파일 저장</button>
  <button type="button" class="ghost" id="btn-copy">내용 복사</button>
  <button type="button" class="ghost" id="btn-print">인쇄</button>
  <button type="button" class="ghost" id="btn-reset">전체 지우기</button>
</div>
<script>{JS}</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="영양사 피드백 수집용 HTML 폼 생성")
    ap.add_argument("--profiles", default=DEFAULT_PROFILE_KEYS,
                    help="급식 대상 profile_key 를 쉼표로")
    ap.add_argument("--days", type=int, default=3,
                    help="평가할 일수 (기본 3 — 끼니가 많으면 끝까지 못 쓴다)")
    ap.add_argument("--time-limit", type=float, default=60.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    profiles = up.load_profiles()
    menus = cs.load_menus()
    engine = cs.get_engine()
    sodium_by_idx, _ = scd.load_nutrition_fields(engine, menus)
    main_by_idx = scd.load_main_ingredients(engine, menus)
    affinity_table = ma.load_affinity_table()
    by_name = {}
    for m in menus:
        by_name.setdefault(m.name, m)
    print(f"[후보] {len(menus)}종 · 어울림 근거 {len(affinity_table)}행")

    sections, used, seen_menus = [], [], {}
    for key in [p.strip() for p in args.profiles.split(",") if p.strip()]:
        prof = profiles.get(key)
        if prof is None:
            print(f"  [skip] 미정의 프로파일: {key}")
            continue
        res, meals, tg = solve(menus, prof, args.days, args.time_limit,
                               sodium_by_idx, main_by_idx, affinity_table)
        print(f"  [{prof.group_name} {len(meals)}식] {res.status} {res.wall_time:.1f}초")
        if not res.plan:
            continue
        used.append(prof)
        plan = {}
        for day, mm in res.plan.items():
            plan[day] = {}
            for mname, picks in mm.items():
                pairs = []
                for n in picks:
                    cat = by_name[n].category if n in by_name else "?"
                    pairs.append((n, cat))
                    if cat in ("주찬", "부찬", "김치"):
                        seen_menus[n] = cat
                plan[day][mname] = pairs
        sections.append({
            "key": key, "profile_name": prof.group_name, "meals": meals,
            "plan": plan, "daily_kcal": res.daily_kcal,
            "kcal": f"{tg['target_kcal_per_day']:,.0f}",
            "sodium": f"{tg['sodium_max_mg_per_day']:,.0f}",
            "basis": tg["basis"],
        })

    if not sections:
        print("[중단] 생성된 식단이 없습니다.")
        return 1

    # 감점 중인 조리법 = 근거표에서 계수가 붙은 것만. 폼에 "안 막는 축"을 섞으면
    # 영양사가 이미 막고 있다고 오해한다.
    w = ma.AffinityWeights()
    coefs = []
    for r in ma._usable(affinity_table, ma.AXIS_METHOD, w):
        if r.value_a == r.value_b:
            c = ma._coef(r.score, w.w_method_same, w.score_divisor)
            if c:
                coefs.append((r.value_a, r.lift, c))
    coefs.sort(key=lambda t: t[2])

    n_meals = sum(len(mm) for s in sections for mm in s["plan"].values())
    meta = {
        "form_version": FORM_VERSION, "generated": date.today().isoformat(),
        "n_menus": len(menus), "n_meals": n_meals,
        "profiles": [s["key"] for s in sections],
    }
    cat_sample = sorted(seen_menus.items())
    out = Path(args.out) if args.out else (
        OUT_DIR / f"menu_feedback_{date.today().strftime('%Y%m%d')}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(meta, sections, cat_sample, coefs, used), encoding="utf-8")
    print(f"[완료] {out.relative_to(REPO_ROOT)} "
          f"({out.stat().st_size:,} bytes · {n_meals}끼니 · 분류확인 {len(cat_sample)}종)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
