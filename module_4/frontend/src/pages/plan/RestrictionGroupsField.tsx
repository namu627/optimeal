// src/pages/plan/RestrictionGroupsField.tsx
// 조건 입력 화면 · 제한 조건 그룹(알레르기·기저질환) — 시안 2a(시안 A + 시안 B의 합계 행·태그 접기).
//  · 한 줄 = 한 그룹. 알레르기(19종 중 다중 선택)와 기저질환(고혈압·당뇨, 신장질환은 준비 중)을 같이 고른다.
//  · 알레르기 태그는 3개까지만 보이고 나머지는 '+N' — 입력칸을 누르면 모두 펼친다.
//  · 그룹 인원 합이 전체 인원을 넘으면 머리말 배지·합계 행이 빨갛게 바뀐다.
//  · 검사(조건 없는 그룹 등)는 Step1Conditions 가 식단 생성을 누를 때 하고, 문제 줄을 errorRows 로 넘겨 빨간 테두리를 그린다.
import { useState } from 'react';
import { Card, Select, InputNumber, Button, Checkbox, Tooltip } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { ALLERGEN_POOL, DISEASE_OPTIONS, dietKindOf, type DietGroup, type DietKind } from '../../api/menu';
import { emptyGroup, groupTotal } from './dietGroups';

const C = {
  text: '#16211C', sub: '#5D6B64', muted: '#98A5A0', border: '#E5EAE7', line: '#EEF2F0',
  green: '#12A150', greenText: '#0B6B36', tint: '#E4F7EB', tintBorder: '#BFEACF', head: '#F7FAF8',
  red: '#E5484D', redText: '#B42318', redTint: '#FDECEC', redBorder: '#F8D0D1',
};
const TAG_LIMIT = 3;
// 그룹 | 알레르기 | 기저질환 | 인원 | 삭제
const COLS = '132px minmax(0, 1fr) 272px 92px 28px';

const KIND_BADGE: Record<DietKind, { icon: string; text: string; style: React.CSSProperties }> = {
  알레르기: { icon: '⊘', text: '알레르기', style: { color: C.greenText, background: C.tint, border: `1px solid ${C.tint}` } },
  기저질환: { icon: '♡', text: '기저질환', style: { color: C.sub, background: C.line, border: `1px solid ${C.line}` } },
  '알레르기+기저질환': { icon: '⊘♡', text: '복합', style: { color: C.greenText, background: '#fff', border: `1px solid ${C.tintBorder}` } },
};

export function KindBadge({ kind }: { kind: DietKind | null }) {
  if (!kind) return <span style={{ fontSize: 11, color: C.muted }}>조건 없음</span>;
  const b = KIND_BADGE[kind];
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 11, fontWeight: 600, borderRadius: 6, padding: '1px 6px', whiteSpace: 'nowrap', ...b.style }}>
      <span aria-hidden>{b.icon}</span>{b.text}
    </span>
  );
}

export default function RestrictionGroupsField({ value, onChange, headcount, errorRows, errorText }: {
  value: DietGroup[]; onChange: (next: DietGroup[]) => void; headcount: number;
  /** 빨간 테두리를 그릴 줄(0부터의 위치) */
  errorRows?: Set<number>;
  /** 식단 생성을 막은 이유(눌렀을 때만) */
  errorText?: string | null;
}) {
  // 알레르기 입력칸을 누르고 있는 줄 — 그 줄만 태그를 모두 펼친다.
  const [focused, setFocused] = useState<number | null>(null);
  const total = groupTotal(value);
  const over = Math.max(0, total - headcount);
  const pct = headcount > 0 ? Math.round((total / headcount) * 100) : 0;
  const rest = Math.max(0, headcount - total);
  const edit = (gi: number, patch: Partial<DietGroup>) => onChange(value.map((g, i) => (i === gi ? { ...g, ...patch } : g)));
  // 지운 뒤에도 '그룹 1, 2, 3…' 으로 다시 매긴다(검사 문구·결과 화면 이름과 맞도록).
  const relabel = (list: DietGroup[]) => list.map((g, i) => ({ ...g, label: `그룹 ${i + 1}` }));
  const toggleDisease = (gi: number, name: string) => {
    const d = value[gi].diseases;
    edit(gi, { diseases: d.includes(name) ? d.filter((x) => x !== name) : DISEASE_OPTIONS.map((o) => o.name).filter((x) => d.includes(x) || x === name) });
  };

  return (
    <Card size="small">
      {/* 머리말 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 15, fontWeight: 600, color: C.text }}>제한 조건 그룹</span>
        <span style={{ fontSize: 12, borderRadius: 8, padding: '2px 8px', ...(over ? { color: C.redText, background: C.redTint } : { color: C.sub, background: C.line }) }}>
          {value.length}그룹 · {total.toLocaleString()} / {headcount.toLocaleString()}명{over ? ` · ${over.toLocaleString()}명 초과` : ''}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 72, height: 4, borderRadius: 2, background: C.line, overflow: 'hidden' }}>
            <span style={{ display: 'block', height: '100%', width: `${Math.min(100, pct)}%`, background: over ? C.red : C.green }} />
          </span>
          <span style={{ fontSize: 11, color: over ? C.redText : C.muted }}>{pct}%</span>
        </span>
        <span style={{ fontSize: 12, color: C.muted }}>한 사람은 한 그룹에만 · 알레르기 {ALLERGEN_POOL.length}종 · 기저질환 {DISEASE_OPTIONS.filter((o) => !o.pending).length}종 지원</span>
        <div style={{ flex: 1 }} />
        <Button size="small" icon={<PlusOutlined />} onClick={() => onChange([...value, emptyGroup(value.length)])}>그룹 추가</Button>
      </div>

      {/* 열 이름 */}
      <div style={{ display: 'grid', gridTemplateColumns: COLS, gap: 12, padding: '0 12px 6px', fontSize: 11, color: C.muted }}>
        <span>그룹</span><span>알레르기</span><span>기저질환</span><span>인원</span><span />
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {value.map((g, gi) => {
          const bad = errorRows?.has(gi);
          const open = focused === gi;
          return (
            <div key={gi} data-testid={`diet-group-${gi}`}
              style={{ display: 'grid', gridTemplateColumns: COLS, gap: 12, alignItems: 'center', borderRadius: 10, padding: '8px 12px',
                border: `1px solid ${bad ? C.red : C.line}`, boxShadow: bad ? `0 0 0 2px ${C.redTint}` : undefined }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                <span style={{ fontSize: 12, color: C.sub, whiteSpace: 'nowrap' }}>그룹 {gi + 1}</span>
                <KindBadge kind={dietKindOf(g)} />
              </div>
              <Select<string[]> mode="multiple" size="small" variant="borderless" placeholder="알레르기 추가…"
                value={g.allergens} style={{ width: '100%' }}
                maxTagCount={open ? undefined : TAG_LIMIT}
                maxTagPlaceholder={(omitted) => `+${omitted.length}`}
                onFocus={() => setFocused(gi)} onBlur={() => setFocused((f) => (f === gi ? null : f))}
                options={ALLERGEN_POOL.map((a) => ({ value: a, label: a }))}
                onChange={(allergens) => edit(gi, { allergens: ALLERGEN_POOL.filter((a) => allergens.includes(a)) })}
                tagRender={({ label, closable, onClose }) => (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: C.greenText, background: C.tint, borderRadius: 6, padding: '1px 6px', marginInlineEnd: 4 }}>
                    {label}
                    {closable && <span role="button" aria-label="삭제" style={{ cursor: 'pointer', fontSize: 10 }}
                      onMouseDown={(e) => e.preventDefault()} onClick={onClose}>✕</span>}
                  </span>
                )}
              />
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, whiteSpace: 'nowrap' }}>
                {DISEASE_OPTIONS.map((o) => o.pending ? (
                  <Tooltip key={o.name} title="기준을 정하는 중이라 아직 선택할 수 없습니다">
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: C.muted }}>
                      <Checkbox disabled /> {o.name} <span style={{ fontSize: 10, background: C.line, borderRadius: 4, padding: '0 4px' }}>준비 중</span>
                    </span>
                  </Tooltip>
                ) : (
                  <Checkbox key={o.name} checked={g.diseases.includes(o.name)} onChange={() => toggleDisease(gi, o.name)}>
                    <span style={{ fontSize: 12 }}>{o.name}</span>
                  </Checkbox>
                ))}
              </div>
              <InputNumber size="small" value={g.count} min={0} onChange={(v) => edit(gi, { count: Number(v) || 0 })} suffix="명" style={{ width: '100%' }} />
              <DeleteOutlined aria-label="그룹 삭제" style={{ color: C.muted, cursor: 'pointer' }}
                onClick={() => onChange(relabel(value.length > 1 ? value.filter((_, i) => i !== gi) : [emptyGroup(0)]))} />
            </div>
          );
        })}
      </div>

      {/* 합계 행 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 10, padding: '8px 12px', borderRadius: 10, fontSize: 12,
        ...(over ? { background: C.redTint, color: C.redText } : { background: C.head, color: C.sub }) }}>
        <span style={{ fontWeight: 600 }}>합계 · {value.length}그룹</span>
        <span style={{ flex: 1 }}>
          {over
            ? `전체 인원보다 ${over.toLocaleString()}명 많습니다 · 겹치는 사람은 복합 그룹 하나로 옮겨 주세요`
            : `나머지 ${rest.toLocaleString()}명은 일반식`}
        </span>
        <span style={{ fontWeight: 600 }}>{total.toLocaleString()} / {headcount.toLocaleString()}명</span>
      </div>

      {errorText && <div role="alert" style={{ marginTop: 8, fontSize: 12, color: C.redText }}>{errorText}</div>}
    </Card>
  );
}
