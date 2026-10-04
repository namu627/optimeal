import { api } from './client';

/** ok = 백엔드·DB 모두 정상, db_down = 백엔드는 떴지만 영양성분 DB 연결 안 됨, down = 백엔드 연결 실패. */
export type HealthState = 'ok' | 'db_down' | 'down';

export async function checkHealth(): Promise<HealthState> {
  try {
    // 헬스 체크는 조용히 — 인터셉터의 '서버에 연결할 수 없어요' 토스트를 30초마다 띄우지 않는다.
    const { data } = await api.get<{ db?: string }>('/health', { silent: true });
    // 구버전 백엔드(db 필드 없음)는 DB 를 확인하지 못하므로 정상으로 둔다.
    return data?.db === 'down' ? 'db_down' : 'ok';
  } catch {
    return 'down';
  }
}
