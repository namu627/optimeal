import axios from 'axios';
import { message } from 'antd';

declare module 'axios' {
  interface AxiosRequestConfig {
    /** true 면 공통 오류 토스트를 띄우지 않는다 — 호출부가 화면에서 직접 안내하는 요청(헬스 체크·식단 생성). */
    silent?: boolean;
  }
}

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? 'http://localhost:8000',
  // 식단 생성은 솔버 상한(백엔드 기본: 7일 이하 30초 · 31일 60초) + 후보 조회·대체식 계산이 붙는다.
  // 31일은 그 부가 시간이 10~16초라 70초면 끝난 식단을 받기 전에 끊길 수 있다.
  timeout: 90000,
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.config?.silent) return Promise.reject(err);
    if (err.code === 'ECONNABORTED') message.error('요청 시간이 초과됐어요.');
    else if (err.response?.status === 503) message.warning('백엔드 준비 중입니다.');
    else if (!err.response) message.error('서버에 연결할 수 없어요.');
    return Promise.reject(err);
  }
);