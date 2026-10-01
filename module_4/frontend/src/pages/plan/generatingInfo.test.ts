import { describe, expect, it } from 'vitest';
import { estimateSeconds } from './generatingInfo';

// 생성 중 화면의 '예상 약 N초' — 측정값 기준 구간(1일·7일·31일 × 1식/2식 이상).
describe('estimateSeconds', () => {
  it('기간·끼니 수로 예상 시간을 정한다', () => {
    expect(estimateSeconds(1, 1)).toBe(10);
    expect(estimateSeconds(1, 3)).toBe(10);
    expect(estimateSeconds(7, 1)).toBe(25);
    expect(estimateSeconds(7, 2)).toBe(40);
    expect(estimateSeconds(7, 3)).toBe(40);
    expect(estimateSeconds(31, 1)).toBe(50);
    expect(estimateSeconds(31, 2)).toBe(60);
  });
});
