import { useEffect, useState } from 'react';
import { checkHealth } from './health';

export function useHealth() {
  const [ready, setReady] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    const run = () => checkHealth().then((ok) => alive && setReady(ok));
    run();
    const timer = setInterval(run, 30000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return ready;
}