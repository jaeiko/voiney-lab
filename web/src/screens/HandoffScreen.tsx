import { useEffect } from "react";

import { LoadingBox } from "../components/Pieces";
import { states } from "../copy/ko";
import { goToOldScreen } from "../lib/navigation";

/** A route the new screen has not taken over yet opens on the old screen, unchanged. */
export function HandoffScreen({ hash }: { hash: string }) {
  useEffect(() => {
    goToOldScreen(hash);
  }, [hash]);
  return <LoadingBox label={states.handoff} />;
}
