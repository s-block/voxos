import { useCallback, useEffect, useState } from "react";
import { loadAgentImprovements, loadLearningSettings, saveLearningSettings, reviewAgentImprovement, loadImprovementEvidence, type AgentImprovement, type ImprovementEvidence } from "../api";

export function AgentImprovements({ canEdit }: { canEdit: boolean }) {
  const [proposals, setProposals] = useState<AgentImprovement[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [automatic, setAutomatic] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<Record<string, ImprovementEvidence>>({});
  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [items, settings] = await Promise.all([loadAgentImprovements(), loadLearningSettings()]);
      setProposals(items); setHasMore(items.length === 50); setEvidence({}); setAutomatic(settings.automatic); setError(null);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Improvements could not be loaded"); }
    finally { setBusy(false); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => { void refresh(); }, 0); return () => window.clearTimeout(timer); }, [refresh]);
  async function loadMore() {
    setBusy(true);
    try {
      const items = await loadAgentImprovements(proposals[proposals.length - 1]);
      setProposals(previous => [...previous, ...items]); setHasMore(items.length === 50); setError(null);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "More improvements could not be loaded"); }
    finally { setBusy(false); }
  }
  async function changeSetting(value: boolean) {
    setBusy(true);
    try { setAutomatic((await saveLearningSettings(value)).automatic); setError(null); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Learning setting could not be saved"); }
    finally { setBusy(false); }
  }
  async function review(proposal: AgentImprovement, action: "approve" | "reject" | "undo") {
    setBusy(true);
    try { const updated = await reviewAgentImprovement(proposal, action); setProposals(items => items.map(item => item.id === updated.id ? updated : item)); setError(null); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Review could not be saved"); }
    finally { setBusy(false); }
  }
  async function readEvidence(id: string) {
    try { const result = await loadImprovementEvidence(id); setEvidence(items => ({ ...items, [id]: result })); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Evidence could not be loaded"); }
  }
  return <section className="panel">
    <div className="panel-heading"><h2>Agent improvements</h2><button disabled={busy} onClick={() => void refresh()}>Refresh</button></div>
    <p>Learning is shared across your account’s agents. Uncertain lessons and revisions stay here for review.</p>
    <label><input type="checkbox" checked={automatic} disabled={!canEdit || busy} onChange={event => void changeSetting(event.target.checked)} /> Automatically apply well-supported, low-risk new memories and skills</label>
    <p>Automatic learning requires cited evidence and an independent model assessment. Existing knowledge revisions require your review.</p>
    {error && <p role="alert" className="error-text">{error}</p>}
    {proposals.length === 0 && !busy && <p>No improvements yet. Ask an agent to investigate a failure or record an evidenced lesson.</p>}
    {proposals.map(proposal => <article className="panel" key={proposal.id}>
      <h3>{proposal.candidate?.payload.kind ?? "Withdrawn improvement"} · {proposal.status}</h3>
      <p>{proposal.candidate?.reason}</p>
      <p>{proposal.reasons.map(reason => reason.replaceAll("_", " ")).join("; ")}</p>
      {proposal.decision_mode && <p>Decision: {proposal.decision_mode.replaceAll("_", " ")} · {proposal.policy_version}</p>}
      {proposal.assessment && <p>Assessment: {proposal.assessment.reason}</p>}
      {proposal.candidate && <details open><summary>Proposed content</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(proposal.candidate.payload, null, 2)}</pre></details>}
      {Object.keys(proposal.before).length > 0 && <details><summary>Previous content</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(proposal.before, null, 2)}</pre></details>}
      {Object.keys(proposal.after).length > 0 && <details><summary>Applied content</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(proposal.after, null, 2)}</pre></details>}
      {proposal.candidate && <button onClick={() => void readEvidence(proposal.id)}>Read cited evidence</button>}
      {evidence[proposal.id] && <div><p>{evidence[proposal.id].coverage}</p>{evidence[proposal.id].items.map(item => <blockquote key={`${item.reference.kind}:${item.reference.key}`}><p>{item.summary}</p><small>{item.reference.kind}:{item.reference.key} · {item.category} · {item.created_at}</small></blockquote>)}{evidence[proposal.id].unavailable > 0 && <p>Some cited evidence is unavailable.</p>}</div>}
      {canEdit && proposal.content_hash && proposal.status === "proposed" && <div className="button-row"><button disabled={busy || proposal.candidate?.payload.kind === "diagnostic"} onClick={() => void review(proposal, "approve")}>Approve</button><button disabled={busy} onClick={() => void review(proposal, "reject")}>Reject</button></div>}
      {canEdit && proposal.content_hash && proposal.status === "applied" && <button disabled={busy} onClick={() => void review(proposal, "undo")}>Undo</button>}
    </article>)}
    {hasMore && <button disabled={busy} onClick={() => void loadMore()}>Load older improvements</button>}
  </section>;
}
