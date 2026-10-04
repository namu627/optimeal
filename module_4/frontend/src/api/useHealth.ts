import { useEffect, useState } from 'react';
import { checkHealth, type HealthState } from './health';

/** null = 아직 확인 전. 30초마다 다시 확인한다(DB 컨테이너를 켜고 끄면 반영). */
export function useHealth() {
  const [state, setState] = useState<HealthState | null>(null);

  useEffect(() => {
    let alive = true;
    const run = () => checkHealth().then((s) => alive && setState(s));
    run();
    const timer = setInterval(run, 30000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return state;
}
