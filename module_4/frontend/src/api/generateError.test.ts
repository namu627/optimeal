import { describe, expect, it } from 'vitest';
import { classifyGenerateError, USE_MOCK } from './menu';

// 생성 실패는 목업으로 감추지 않고 원인별 실패 화면으로 — 원인 구분이 맞는지 확인한다.
describe('classifyGenerateError', () => {
  it('응답이 없으면 백엔드 연결 실패', () => {
    expect(classifyGenerateError({ message: 'Network Error', code: 'ERR_NETWORK' }).kind).toBe('network');
  });
  it('클라이언트 타임아웃은 서버 오류(응답 시간 초과)', () => {
    const e = classifyGenerateError({ code: 'ECONNABORTED', message: 'timeout of 90000ms exceeded' });
    expect(e.kind).toBe('server');
    expect(e.detail).toContain('시간 초과');
  });
  it('503 menu_source_unavailable 은 DB 연결 실패', () => {
    const e = classifyGenerateError({ response: { status: 503, data: { detail: {
      reason: 'menu_source_unavailable', message: '메뉴 후보 조회 실패: connection refused\n\tIs the server running' } } } });
    expect(e).toEqual({ kind: 'db', detail: '메뉴 후보 조회 실패: connection refused' });
  });
  it('그 밖의 503·500 은 서버 오류', () => {
    expect(classifyGenerateError({ response: { status: 503, data: { detail: { reason: 'module_3_not_found', message: 'x' } } } }))
      .toEqual({ kind: 'server', detail: 'HTTP 503 · module_3_not_found — x' });
    expect(classifyGenerateError({ response: { status: 500, data: { detail: 'Internal Server Error' } } }).kind).toBe('server');
  });
  it('목업 스위치는 기본 꺼짐', () => {
    expect(USE_MOCK).toBe(false);
  });
});
