import { Button, InputNumber, Select, Tag } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { colors } from '../theme';
import type { AllergyGroup } from '../api/menu';

/**
 * 알레르기 유발물질 목록.
 * 시안 04 에 적힌 항목을 그대로 옮겼다.
 * 식약처 기준 몇 종으로 갈지 팀 확정되면 이 배열만 고치면 된다.
 */
export const ALLERGENS = [
  '난류',
  '우유',
  '땅콩',
  '대두',
  '밀',
  '갑각류',
  '고등어',
  '새우',
  '복숭아',
  '토마토',
] as const;

export interface AllergyGroupsFieldProps {
  value: AllergyGroup[];
  onChange: (next: AllergyGroup[]) => void;
  /** 그룹 인원 합이 이 값을 넘으면 경고 */
  headcount?: number;
  error?: string;
  disabled?: boolean;
}

function groupLabel(allergens: string[]): string {
  return allergens.join('·');
}

export default function AllergyGroupsField({
  value,
  onChange,
  headcount,
  error,
  disabled = false,
}: AllergyGroupsFieldProps) {
  const totalPeople = value.reduce((sum, g) => sum + (g.count || 0), 0);

  const update = (idx: number, patch: Partial<AllergyGroup>) => {
    onChange(
      value.map((g, i) => {
        if (i !== idx) return g;
        const next = { ...g, ...patch };
        return { ...next, label: groupLabel(next.allergens) };
      }),
    );
  };

  const addGroup = () => onChange([...value, { label: '', allergens: [], count: 1 }]);
  const removeGroup = (idx: number) => onChange(value.filter((_, i) => i !== idx));

  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 10,
          marginBottom: 4,
          flexWrap: 'wrap',
        }}
      >
        <span style={{ fontSize: 14, fontWeight: 600, color: colors.text }}>알레르기 그룹</span>
        <span style={{ fontSize: 12, color: colors.textSecondary }}>
          {value.length}그룹 · {totalPeople}명
        </span>
      </div>
      <div style={{ fontSize: 12, color: colors.textTertiary, marginBottom: 12 }}>
        {ALLERGENS.join(' · ')} 중 선택
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {value.map((group, idx) => (
          <div
            key={idx}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '12px 14px',
              border: `1px solid ${colors.border}`,
              borderRadius: 12,
              background: colors.bgLayout,
            }}
          >
            <span
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: colors.textSecondary,
                whiteSpace: 'nowrap',
              }}
            >
              그룹 {idx + 1}
            </span>

            <Select
              mode="multiple"
              allowClear
              disabled={disabled}
              placeholder="알레르기 추가…"
              value={group.allergens}
              onChange={(allergens: string[]) => update(idx, { allergens })}
              options={ALLERGENS.map((a) => ({ value: a, label: a }))}
              style={{ flex: 1, minWidth: 0 }}
              tagRender={({ label, onClose }) => (
                <Tag closable onClose={onClose} style={{ marginInlineEnd: 4 }}>
                  {label}
                </Tag>
              )}
            />

            <InputNumber
              disabled={disabled}
              min={1}
              max={9999}
              value={group.count}
              onChange={(count) => update(idx, { count: count ?? 1 })}
              addonAfter="명"
              style={{ width: 110 }}
            />

            <Button
              type="text"
              disabled={disabled}
              onClick={() => removeGroup(idx)}
              style={{ color: colors.textTertiary }}
            >
              삭제
            </Button>
          </div>
        ))}
      </div>

      <Button
        type="dashed"
        icon={<PlusOutlined />}
        disabled={disabled}
        onClick={addGroup}
        style={{ marginTop: 10 }}
      >
        그룹 추가
      </Button>

      {headcount != null && totalPeople > headcount && (
        <div style={{ marginTop: 8, fontSize: 12, color: colors.warningText }}>
          알레르기 인원 합({totalPeople}명)이 전체 인원({headcount}명)보다 많습니다.
        </div>
      )}
      {error && <div style={{ marginTop: 8, fontSize: 12, color: colors.error }}>{error}</div>}
    </div>
  );
}