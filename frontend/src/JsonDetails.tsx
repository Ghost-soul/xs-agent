import { useMemo, useState } from "react";

/** Large audit payloads are formatted and mounted only while the reader opens them. */
export function JsonDetails({ title, value }: { title: string; value: unknown }) {
  const [open, setOpen] = useState(false);
  const formatted = useMemo(() => open ? JSON.stringify(value, null, 2) : "", [open, value]);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{title}</summary>{open && <pre>{formatted}</pre>}
  </details>;
}
