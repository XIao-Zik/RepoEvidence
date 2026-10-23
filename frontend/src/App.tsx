import { useEffect, useState } from "react";
import { PanelLeftClose, AlertCircle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import { EventDialog } from "@/components/event-dialog";
import {
  EvidenceDialog,
  type FrozenEvidence,
} from "@/components/evidence-dialog";
import { SessionNavigation } from "@/components/session-navigation";
import { RepositoryPanel } from "@/components/repository-panel";
import { ChatPanel } from "@/components/chat-panel";
import { RunTrace } from "@/components/run-trace";
import { useSessionCatalog } from "@/hooks/use-session-catalog";
import { useSessionEvents } from "@/hooks/use-session-events";
import { useRunActions } from "@/hooks/use-run-actions";
import { useTrace } from "@/hooks/use-trace";
import { useRepositories } from "@/hooks/use-repositories";
import { repositoryApi } from "@/lib/session-api";
import { objectValue, textValue, type EvidenceView } from "@/event-data";

// 页面根组件只负责布局、响应式切换与检查弹窗。
export default function App() {
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const catalog = useSessionCatalog(retry, setError);
  const { meta, activeId: active } = catalog;
  const repositories = useRepositories(retry, setError);
  const feed = useSessionEvents(active, retry, catalog.refresh, setError);
  const actions = useRunActions({
    sessionId: active,
    blocked: catalog.deleting,
    status: feed.status,
    repository: repositories.readySelection,
    selectSession: catalog.selectSession,
    refreshSessions: catalog.refresh,
    reportError: setError,
  });
  const reconnect = () => setRetry((value) => value + 1);
  const [tab, setTab] = useState<"chat" | "trace">("chat");
  const [sidebar, setSidebar] = useState(false);
  const [runFilter, setRunFilter] = useState("all");
  // 轨迹要展开完整上下文时，侧栏宽度不够；宽屏档把对话让出去，不引入分栏依赖。
  const [wideTrace, setWideTrace] = useState(false);
  const [modal, setModal] = useState<{ title: string; data: unknown } | null>(
    null,
  );
  const [evidenceModal, setEvidenceModal] = useState<{
    evidence: FrozenEvidence | null;
    error: string | null;
  } | null>(null);
  useEffect(() => {
    setRunFilter("all");
  }, [active]);
  const inspect = (title: string, data: unknown) => setModal({ title, data });
  const inspectEvidence = (item: EvidenceView) => {
    const source = objectValue(item.source);
    const locator = objectValue(source.locator);
    const path = textValue(source.path);
    const collectionId = textValue(source.collection_id);
    const start = locator.start_line;
    const end = locator.end_line;
    const passageId = item.id;
    const repositoryId = repositories.readySelection?.repositoryId;
    if (!repositoryId || !/^doc:.+(?:#|@)c0*[1-9][0-9]*$/.test(passageId)) {
      inspect(
        path && typeof start === "number" && typeof end === "number"
          ? `${path}:${start}-${end}`
          : textValue(item.title) || item.id,
        item,
      );
      return;
    }
    setEvidenceModal({ evidence: null, error: null });
    void repositoryApi
      .getEvidence(repositoryId, passageId, collectionId || undefined)
      .then(
        (evidence) => setEvidenceModal({ evidence, error: null }),
        (error: unknown) =>
          setEvidenceModal({
            evidence: null,
            error: error instanceof Error ? error.message : "无法读取冻结证据",
          }),
      );
  };
  const projection = useTrace(feed.events, runFilter);
  const { traceEntries } = projection;
  const repositoryPanel = (
    <RepositoryPanel
      roots={repositories.roots}
      repositories={repositories.repositories}
      selected={repositories.selected}
      disabled={actions.pending || catalog.deleting}
      onSelect={repositories.select}
      onRegister={repositories.register}
      onScan={repositories.scan}
      onStartIndex={repositories.startIndex}
      onPrunePreview={repositories.prunePreview}
      onPrune={repositories.prune}
      onError={setError}
    />
  );
  const sessionNavigation = (
    <SessionNavigation
      sessions={catalog.sessions}
      activeId={active}
      disabled={actions.pending || !meta || !repositories.readySelection}
      deleting={catalog.deleting}
      deleteError={catalog.deleteError}
      onDelete={catalog.deleteSessions}
      onSelect={(id) => {
        catalog.selectSession(id);
        setSidebar(false);
      }}
      onCreate={async () => {
        if (await actions.createSession()) setSidebar(false);
      }}
    />
  );
  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      <aside className="hidden w-[280px] shrink-0 flex-col border-r bg-muted/70 px-4 pt-6 lg:flex">
        {repositoryPanel}
        {sessionNavigation}
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        {/* 顶栏只留当前阶段：品牌与路径已在侧栏，重复一遍会挤掉内容高度。 */}
        <header className="flex h-12 shrink-0 items-center gap-2.5 border-b px-3 sm:px-5">
          <Sheet open={sidebar} onOpenChange={setSidebar}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="-ml-1 lg:hidden"
                aria-label="切换会话列表"
              >
                <PanelLeftClose />
              </Button>
            </SheetTrigger>
            <SheetContent
              side="left"
              className="w-[min(320px,90vw)] gap-0 bg-muted p-4"
            >
              <SheetHeader className="sr-only">
                <SheetTitle>会话列表</SheetTitle>
                <SheetDescription>新建或恢复本地调查会话。</SheetDescription>
              </SheetHeader>
              {repositoryPanel}
              {sessionNavigation}
            </SheetContent>
          </Sheet>
          <Badge
            variant="outline"
            className={cn(
              "shrink-0 gap-1.5 px-2.5 py-1 text-[11px] font-medium",
              meta
                ? "border-primary/25 bg-primary/8 text-primary"
                : "border-border bg-muted text-muted-foreground",
            )}
          >
            <span className="size-1.5 rounded-full bg-current" />
            {meta?.workflow.label ?? "连接后端中"}
          </Badge>
          {meta && (
            <span className="truncate font-mono text-[11px] text-muted-foreground">
              {meta.model}
            </span>
          )}
        </header>
        {error && (
          <Alert
            variant="destructive"
            className="flex shrink-0 items-center gap-3 rounded-none border-x-0 border-t-0 bg-red-50/60 px-5 py-3"
          >
            <AlertCircle className="size-4" />
            <AlertDescription className="min-w-0 flex-1 text-xs">
              {error}
            </AlertDescription>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                reconnect();
                setError("");
              }}
            >
              重新连接
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label="关闭错误提示"
              onClick={() => setError("")}
            >
              <X />
            </Button>
          </Alert>
        )}
        <Tabs
          value={tab}
          onValueChange={(value) => setTab(value as "chat" | "trace")}
          className="flex min-h-0 flex-1 flex-col gap-0"
        >
          <TabsList className="mx-4 my-2 grid h-9 shrink-0 grid-cols-2 md:hidden">
            <TabsTrigger value="chat">对话</TabsTrigger>
            <TabsTrigger value="trace">
              运行轨迹 {traceEntries.length > 0 && `(${traceEntries.length})`}
            </TabsTrigger>
          </TabsList>
          <div className="flex min-h-0 flex-1">
            <TabsContent
              forceMount
              value="chat"
              className={cn(
                "m-0 flex min-h-0 min-w-0 flex-1 flex-col data-[state=inactive]:hidden md:data-[state=inactive]:flex",
                wideTrace && "md:hidden",
              )}
            >
              <ChatPanel
                meta={meta}
                title={
                  catalog.sessions.find((item) => item.id === active)?.title
                }
                sessionId={active}
                events={feed.events}
                status={feed.status}
                connection={feed.connection}
                loading={catalog.loading}
                actions={actions}
                inspect={inspect}
                inspectEvidence={inspectEvidence}
              />
            </TabsContent>
            <TabsContent
              forceMount
              value="trace"
              className={cn(
                "m-0 flex min-h-0 min-w-0 flex-1 flex-col data-[state=inactive]:hidden md:border-l md:data-[state=inactive]:flex",
                wideTrace
                  ? "md:flex-1"
                  : "md:w-80 md:flex-none xl:w-[370px] 2xl:w-[410px]",
              )}
            >
              <RunTrace
                key={active}
                sessionId={active}
                eventCount={feed.events.length}
                projection={projection}
                runFilter={runFilter}
                setRunFilter={setRunFilter}
                wide={wideTrace}
                setWide={setWideTrace}
                inspect={inspect}
              />
            </TabsContent>
          </div>
        </Tabs>
      </main>
      {modal && <EventDialog {...modal} close={() => setModal(null)} />}
      {evidenceModal && (
        <EvidenceDialog
          {...evidenceModal}
          close={() => setEvidenceModal(null)}
        />
      )}
    </div>
  );
}
