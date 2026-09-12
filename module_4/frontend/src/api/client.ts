import axios from 'axios';
import { message } from 'antd';

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? 'http://localhost:8000',
  timeout: 70000,
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.code === 'ECONNABORTED') message.error('요청 시간이 초과됐어요.');
    else if (err.response?.status === 503) message.warning('백엔드 준비 중입니다.');
    else if (!err.response) message.error('서버에 연결할 수 없어요.');
    return Promise.reject(err);
  }
);