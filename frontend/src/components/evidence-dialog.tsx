import { useRef } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export type FrozenEvidence = {
  collection_id: string;
  snapshot_status?: string;
  retention_reason?: string;
  path: string;
  start_line: number;
  end_line: number;
  content: string;
  source_sha256: string;
};

function numberedLines(content: string, startLine: number): string[] {
  return content
    .split("\n")
    .map(
      (line, index) =>
        `${String(startLine + index).padStart(5, " ")} | ${line}`,
    );
}

/** 展示索引快照中的文本，避免浏览器或 API 从可变工作树读取源码。 */
export function EvidenceDialog({
  evidence,
  error,
  close,
}: {
  evidence: FrozenEvidence | null;
  error: string | null;
  close: () => void;
}) {
  const previousFocus = useRef(document.activeElement as HTMLElement | null);
  const title = evidence
    ? `${evidence.path}:${evidence.start_line}-${evidence.end_line}`
    : "加载证据中";
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent
        className="flex max-h-[85dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl"
        showCloseButton={false}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          previousFocus.current?.focus();
        }}
      >
        <DialogHeader className="border-b px-6 py-4 pr-14 text-left">
          <DialogTitle className="text-base">{title}</DialogTitle>
          <DialogDescription className="text-xs">
            {evidence
              ? `冻结索引快照：${evidence.collection_id}${evidence.snapshot_status ? ` · ${evidence.snapshot_status}` : ""}${evidence.retention_reason ? ` · ${evidence.retention_reason}` : ""}`
              : error || "正在读取冻结的索引片段，不会读取当前工作树。"}
          </DialogDescription>
        </DialogHeader>
        <DialogClose asChild>
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-3 right-3"
            aria-label="关闭证据"
          >
            <X />
          </Button>
        </DialogClose>
        <div className="thin-scrollbar min-h-0 flex-1 overflow-auto px-6 py-3">
          {evidence ? (
            <pre className="min-w-max text-xs leading-6 whitespace-pre-wrap">
              {numberedLines(evidence.content, evidence.start_line).join("\n")}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">
              {error || "加载中…"}
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
