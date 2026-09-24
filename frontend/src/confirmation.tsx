import { useEffect, useRef, useState } from "react";

export type ConfirmationOptions = {
  title?: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  requiredText?: string;
};

type ConfirmationRequest = ConfirmationOptions & { resolve: (accepted: boolean) => void };

export function requestConfirmation(options: string | ConfirmationOptions): Promise<boolean> {
  const normalized = typeof options === "string" ? { message: options } : options;
  return new Promise((resolve) => {
    const handled = !window.dispatchEvent(new CustomEvent<ConfirmationRequest>("novel-writer:confirm", {
      cancelable: true,
      detail: { ...normalized, resolve },
    }));
    // Isolated component hosts (including tests) may not mount ConfirmationHost.
    if (!handled) resolve(window["confirm"](normalized.message));
  });
}

export function ConfirmationHost() {
  const [request, setRequest] = useState<ConfirmationRequest | null>(null);
  const [confirmationText, setConfirmationText] = useState("");
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const receive = (event: Event) => {
      event.preventDefault();
      const next = (event as CustomEvent<ConfirmationRequest>).detail;
      setConfirmationText("");
      setRequest((current) => {
        current?.resolve(false);
        return next;
      });
    };
    window.addEventListener("novel-writer:confirm", receive);
    return () => window.removeEventListener("novel-writer:confirm", receive);
  }, []);

  useEffect(() => {
    if (!request) return;
    cancelRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") finish(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [request]);

  function finish(accepted: boolean) {
    const current = request;
    setRequest(null);
    current?.resolve(accepted);
  }

  if (!request) return null;
  const phraseMatches = !request.requiredText || confirmationText === request.requiredText;
  return <div className="dialog-backdrop" role="presentation">
    <section className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="global-confirm-title" aria-describedby="global-confirm-message">
      <div className="dialog-header"><h2 id="global-confirm-title">{request.title ?? "确认操作"}</h2><button type="button" aria-label="关闭" onClick={() => finish(false)}>×</button></div>
      <p id="global-confirm-message" className="confirmation-message">{request.message}</p>
      {request.requiredText && <label>输入“{request.requiredText}”确认<input autoComplete="off" value={confirmationText} onChange={(event) => setConfirmationText(event.target.value)} /></label>}
      <div className="dialog-actions">
        <button ref={cancelRef} className="secondary-button" type="button" onClick={() => finish(false)}>取消</button>
        <button className={request.danger ? "danger-command" : "primary-button"} type="button" disabled={!phraseMatches} onClick={() => finish(true)}>{request.confirmLabel ?? "确认"}</button>
      </div>
    </section>
  </div>;
}
