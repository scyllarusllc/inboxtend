'use client';

import { useEffect } from 'react';

export default function LegacyScripts({ scripts }) {
  useEffect(() => {
    let cancelled = false;
    const loaded = window.__inboxtendScripts || (window.__inboxtendScripts = new Map());
    async function load() {
      for (const src of scripts) {
        if (cancelled) break;
        if (!loaded.has(src)) {
          loaded.set(src, new Promise((resolve, reject) => {
            const element = document.createElement('script');
            element.src = '/static/' + src;
            element.onload = resolve;
            element.onerror = reject;
            document.body.appendChild(element);
          }));
        }
        await loaded.get(src);
      }
    }
    load().catch(error => console.error('Could not load page script:', error));
    return () => { cancelled = true; };
  }, [scripts]);
  return null;
}
