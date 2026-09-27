import { useEffect, useState } from 'react';
import { thumbnailUrl, type StudySummary } from '../contract/meshContract';

/** A study's gold thumbnail (one slice of the de-identified volume), via a presigned URL. */
export default function Thumb({ study, className = 'thumb' }: { study: StudySummary; className?: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void thumbnailUrl(study).then((u) => { if (live) setSrc(u); });
    return () => { live = false; };
  }, [study]);
  return (
    <div className={className} aria-hidden="true">
      {src ? <img src={src} alt="" /> : <span>{(study.body_part ?? 'CT').slice(0, 2)}</span>}
    </div>
  );
}
