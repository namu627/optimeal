import { api } from './client';

export async function checkHealth(): Promise<boolean> {
  try {
    await api.get('/health');
    return true;
  } catch {
    return false;
  }
}