import { FormEvent, useCallback, useEffect, useState } from "react";
import { NavLink } from "react-router";
import {
  Archive,
  CheckCircle2,
  ListChecks,
  Pencil,
  Plus,
  RefreshCcw,
  Save,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  archiveAgentMemory,
  archiveAgentSkill,
  archiveTaskPlan,
  createAgentMemory,
  createAgentSkill,
  createTaskPlan,
  deleteAgentMemory,
  deleteAgentSkill,
  deleteTaskPlan,
  loadAgentMemories,
  loadAgentSkills,
  loadTaskPlans,
  updateAgentMemory,
  updateAgentSkill,
  updateTaskPlan,
  updateTaskStep,
  type AgentMemory,
  type AgentMemoryKind,
  type AgentMemorySensitivity,
  type AgentMemoryStatus,
  type AgentSkill,
  type AgentSkillCategory,
  type TaskPlan,
  type TaskPriority,
  type TaskStatus,
} from "../api";
import { AgentImprovements } from "../components/AgentImprovements";
import type { LogEntry } from "../appTypes";
import { TextAreaField, TextField } from "../components/FormFields";
import { knowledgeTabPaths, type KnowledgeTab } from "../routing";

type AgentKnowledgePageProps = {
  activeTab: KnowledgeTab;
  canEdit: boolean;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

type MemoryDraft = {
  kind: AgentMemoryKind;
  sensitivity: AgentMemorySensitivity;
  status: AgentMemoryStatus;
  text: string;
};

type SkillDraft = {
  name: string;
  slug: string;
  category: AgentSkillCategory;
  description: string;
  bodyMarkdown: string;
};

type TaskDraft = {
  objective: string;
  priority: TaskPriority;
  status: TaskStatus;
  acceptanceCriteria: string;
  steps: string;
  allowParallelSteps: boolean;
};

const memoryKinds: readonly AgentMemoryKind[] = [
  "preference",
  "fact",
  "instruction",
  "relationship",
  "project",
  "task_context",
];

const memorySensitivities: readonly AgentMemorySensitivity[] = [
  "low",
  "personal",
  "sensitive",
];

const memoryStatuses: readonly AgentMemoryStatus[] = [
  "active",
  "proposed",
  "archived",
  "superseded",
  "rejected",
  "hidden",
];

const skillCategories: readonly AgentSkillCategory[] = [
  "general",
  "voice",
  "memory",
  "planning",
  "tool_use",
];

const taskPriorities: readonly TaskPriority[] = ["low", "normal", "high"];

const taskStatuses: readonly TaskStatus[] = [
  "proposed",
  "active",
  "in_progress",
  "blocked",
  "completed",
  "cancelled",
  "archived",
];

const initialMemoryDraft: MemoryDraft = {
  kind: "preference",
  sensitivity: "low",
  status: "active",
  text: "",
};

const initialSkillDraft: SkillDraft = {
  name: "",
  slug: "",
  category: "general",
  description: "",
  bodyMarkdown: "",
};

const initialTaskDraft: TaskDraft = {
  objective: "",
  priority: "normal",
  status: "active",
  acceptanceCriteria: "",
  steps: "",
  allowParallelSteps: false,
};

export function AgentKnowledgePage({
  activeTab,
  canEdit,
  onLog,
}: AgentKnowledgePageProps) {
  const [memories, setMemories] = useState<AgentMemory[]>([]);
  const [skills, setSkills] = useState<AgentSkill[]>([]);
  const [taskPlans, setTaskPlans] = useState<TaskPlan[]>([]);
  const [memoryDraft, setMemoryDraft] =
    useState<MemoryDraft>(initialMemoryDraft);
  const [skillDraft, setSkillDraft] = useState<SkillDraft>(initialSkillDraft);
  const [taskDraft, setTaskDraft] = useState<TaskDraft>(initialTaskDraft);
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [editingSkillSlug, setEditingSkillSlug] = useState<string | null>(null);
  const [editingTaskPlanId, setEditingTaskPlanId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [loadedMemories, loadedSkills, loadedTaskPlans] = await Promise.all([
        loadAgentMemories(undefined, 50),
        loadAgentSkills(),
        loadTaskPlans(undefined, 50),
      ]);
      setMemories(loadedMemories);
      setSkills(loadedSkills);
      setTaskPlans(loadedTaskPlans);
    } catch (loadError) {
      const message = errorMessage(loadError);
      setError(message);
      onLog(`Agent data load failed: ${message}`, "error");
    } finally {
      setIsLoading(false);
    }
  }, [onLog]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refreshData();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [refreshData]);

  const resetMemoryDraft = () => {
    setEditingMemoryId(null);
    setMemoryDraft(initialMemoryDraft);
  };

  const resetSkillDraft = () => {
    setEditingSkillSlug(null);
    setSkillDraft(initialSkillDraft);
  };

  const resetTaskDraft = () => {
    setEditingTaskPlanId(null);
    setTaskDraft(initialTaskDraft);
  };

  const saveMemory = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = memoryDraft.text.trim();
    if (!canEdit || isSaving || text === "") {
      return;
    }

    setIsSaving(true);
    setError(null);
    try {
      if (editingMemoryId === null) {
        const created = await createAgentMemory({
          confirmed: true,
          kind: memoryDraft.kind,
          sensitivity: memoryDraft.sensitivity,
          text,
        });
        setMemories((current) => upsertById(current, created));
        resetMemoryDraft();
        onLog("Memory created.");
      } else {
        const updated = await updateAgentMemory(editingMemoryId, {
          confirmed: memoryDraft.status === "active",
          status: memoryDraft.status,
          text,
        });
        setMemories((current) => upsertById(current, updated));
        onLog("Memory saved.");
      }
    } catch (saveError) {
      const message = errorMessage(saveError);
      setError(message);
      onLog(`Memory save failed: ${message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const saveSkill = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = skillDraft.name.trim();
    const description = skillDraft.description.trim();
    const bodyMarkdown = skillDraft.bodyMarkdown.trim();
    if (!canEdit || isSaving || name === "" || description === "" || bodyMarkdown === "") {
      return;
    }

    setIsSaving(true);
    setError(null);
    try {
      if (editingSkillSlug === null) {
        const created = await createAgentSkill({
          body_markdown: bodyMarkdown,
          category: skillDraft.category,
          confirmed: true,
          description,
          name,
          slug: skillDraft.slug.trim() || null,
        });
        setSkills((current) => upsertBySlug(current, created));
        resetSkillDraft();
        onLog("Skill created.");
      } else {
        const updated = await updateAgentSkill(editingSkillSlug, {
          body_markdown: bodyMarkdown,
          category: skillDraft.category,
          confirmed: true,
          description,
          name,
        });
        setSkills((current) => upsertBySlug(current, updated));
        if (updated.slug !== editingSkillSlug) {
          setEditingSkillSlug(updated.slug);
        }
        onLog("Skill saved.");
      }
    } catch (saveError) {
      const message = errorMessage(saveError);
      setError(message);
      onLog(`Skill save failed: ${message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const saveTaskPlan = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const objective = taskDraft.objective.trim();
    const acceptanceCriteria = linesFromText(taskDraft.acceptanceCriteria);
    const steps = linesFromText(taskDraft.steps);
    if (
      !canEdit ||
      isSaving ||
      objective === "" ||
      (editingTaskPlanId === null && steps.length === 0)
    ) {
      return;
    }

    setIsSaving(true);
    setError(null);
    try {
      if (editingTaskPlanId === null) {
        const created = await createTaskPlan({
          acceptance_criteria: acceptanceCriteria,
          allow_parallel_steps: taskDraft.allowParallelSteps,
          objective,
          priority: taskDraft.priority,
          status: taskDraft.status,
          steps,
        });
        setTaskPlans((current) => upsertById(current, created));
        resetTaskDraft();
        onLog("Task plan created.");
      } else {
        const updated = await updateTaskPlan(editingTaskPlanId, {
          acceptance_criteria: acceptanceCriteria,
          allow_parallel_steps: taskDraft.allowParallelSteps,
          objective,
          priority: taskDraft.priority,
          status: taskDraft.status,
        });
        setTaskPlans((current) => upsertById(current, updated));
        onLog("Task plan saved.");
      }
    } catch (saveError) {
      const message = errorMessage(saveError);
      setError(message);
      onLog(`Task plan save failed: ${message}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const archiveMemory = async (memoryId: string) => {
    if (!canEdit || isSaving) {
      return;
    }
    await runSavingAction({
      action: async () => {
        const archived = await archiveAgentMemory(memoryId);
        setMemories((current) => upsertById(current, archived));
      },
      onLog,
      setError,
      setIsSaving,
      successMessage: "Memory archived.",
      failurePrefix: "Memory archive failed",
    });
  };

  const deleteMemory = async (memoryId: string) => {
    if (!canEdit || isSaving || !window.confirm("Permanently delete this memory?")) {
      return;
    }
    await runSavingAction({
      action: async () => {
        await deleteAgentMemory(memoryId);
        setMemories((current) => removeById(current, memoryId));
        if (editingMemoryId === memoryId) {
          resetMemoryDraft();
        }
      },
      onLog,
      setError,
      setIsSaving,
      successMessage: "Memory deleted.",
      failurePrefix: "Memory delete failed",
    });
  };

  const setMemoryStatus = async (
    memoryId: string,
    memoryStatus: AgentMemoryStatus,
  ) => {
    if (!canEdit || isSaving) {
      return;
    }
    await runSavingAction({
      action: async () => {
        const updated = await updateAgentMemory(memoryId, {
          confirmed: memoryStatus === "active",
          status: memoryStatus,
        });
        setMemories((current) => upsertById(current, updated));
      },
      onLog,
      setError,
      setIsSaving,
      successMessage: "Memory status saved.",
      failurePrefix: "Memory status save failed",
    });
  };

  const archiveSkill = async (slug: string) => {
    if (!canEdit || isSaving) {
      return;
    }
    await runSavingAction({
      action: async () => {
        const archived = await archiveAgentSkill(slug);
        setSkills((current) => upsertBySlug(current, archived));
      },
      onLog,
      setError,
      setIsSaving,
      successMessage: "Skill archived.",
      failurePrefix: "Skill archive failed",
    });
  };

  const deleteSkill = async (slug: string) => {
    if (!canEdit || isSaving || !window.confirm("Permanently delete this skill?")) {
      return;
    }
    await runSavingAction({
      action: async () => {
        await deleteAgentSkill(slug);
        setSkills((current) => removeBySlug(current, slug));
        if (editingSkillSlug === slug) {
          resetSkillDraft();
        }
      },
      onLog,
      setError,
      setIsSaving,
      successMessage: "Skill deleted.",
      failurePrefix: "Skill delete failed",
    });
  };

  const activateSkill = async (slug: string) => {
    if (!canEdit || isSaving) {
      return;
    }
    await runSavingAction({
      action: async () => {
        const updated = await updateAgentSkill(slug, { confirmed: true });
        setSkills((current) => upsertBySlug(current, updated));
      },
      onLog,
      setError,
      setIsSaving,
      successMessage: "Skill activated.",
      failurePrefix: "Skill activation failed",
    });
  };

  const archivePlan = async (planId: string) => {
    if (!canEdit || isSaving) {
      return;
    }
    await runSavingAction({
      action: async () => {
        const archived = await archiveTaskPlan(planId);
        setTaskPlans((current) => upsertById(current, archived));
      },
      onLog,
      setError,
      setIsSaving,
      successMessage: "Task plan archived.",
      failurePrefix: "Task plan archive failed",
    });
  };

  const deletePlan = async (planId: string) => {
    if (!canEdit || isSaving || !window.confirm("Permanently delete this task plan?")) {
      return;
    }
    await runSavingAction({
      action: async () => {
        await deleteTaskPlan(planId);
        setTaskPlans((current) => removeById(current, planId));
        if (editingTaskPlanId === planId) {
          resetTaskDraft();
        }
      },
      onLog,
      setError,
      setIsSaving,
      successMessage: "Task plan deleted.",
      failurePrefix: "Task plan delete failed",
    });
  };

  const setStepStatus = async (stepId: string, stepStatus: TaskStatus) => {
    if (!canEdit || isSaving) {
      return;
    }
    await runSavingAction({
      action: async () => {
        const updated = await updateTaskStep(stepId, { status: stepStatus });
        setTaskPlans((current) => upsertById(current, updated));
      },
      onLog,
      setError,
      setIsSaving,
      successMessage: "Task step saved.",
      failurePrefix: "Task step save failed",
    });
  };

  return (
    <div className="settings-page knowledge-page">
      <section className="settings-panel knowledge-toolbar">
        <div className="section-heading-row">
          <div className="section-title">
            <p className="eyebrow">Agent data</p>
            <h2>Knowledge base</h2>
          </div>
          <button
            aria-busy={isLoading}
            disabled={isLoading || isSaving}
            onClick={() => void refreshData()}
            type="button"
          >
            <RefreshCcw aria-hidden="true" size={16} />
            Refresh
          </button>
        </div>
        <div className="segmented knowledge-tabs">
          <NavLink
            className={({ isActive }) => (isActive ? "selected" : undefined)}
            end
            to={knowledgeTabPaths.memories}
          >
            Memories
          </NavLink>
          <NavLink
            className={({ isActive }) => (isActive ? "selected" : undefined)}
            end
            to={knowledgeTabPaths.skills}
          >
            Skills
          </NavLink>
          <NavLink
            className={({ isActive }) => (isActive ? "selected" : undefined)}
            end
            to={knowledgeTabPaths.tasks}
          >
            Tasks
          </NavLink>
          <NavLink className={({ isActive }) => (isActive ? "selected" : undefined)} end to={knowledgeTabPaths.improvements}>Improvements</NavLink>
        </div>
        {error !== null ? <p className="error-text">{error}</p> : null}
      </section>

      {activeTab === "improvements" ? <AgentImprovements canEdit={canEdit} /> : null}

      {activeTab === "memories" ? (
        <MemoryManager
          canEdit={canEdit}
          draft={memoryDraft}
          editingMemoryId={editingMemoryId}
          isSaving={isSaving}
          memories={memories}
          onArchive={archiveMemory}
          onDelete={deleteMemory}
          onDraftChange={setMemoryDraft}
          onEdit={(memory) => {
            setEditingMemoryId(memory.id);
            setMemoryDraft({
              kind: memory.kind,
              sensitivity: memory.sensitivity,
              status: memory.status,
              text: memory.text,
            });
          }}
          onNew={resetMemoryDraft}
          onSave={saveMemory}
          onSetStatus={setMemoryStatus}
        />
      ) : null}

      {activeTab === "skills" ? (
        <SkillManager
          canEdit={canEdit}
          draft={skillDraft}
          editingSkillSlug={editingSkillSlug}
          isSaving={isSaving}
          onActivate={activateSkill}
          onArchive={archiveSkill}
          onDelete={deleteSkill}
          onDraftChange={setSkillDraft}
          onEdit={(skill) => {
            setEditingSkillSlug(skill.slug);
            setSkillDraft({
              bodyMarkdown: skill.body_markdown,
              category: skill.category,
              description: skill.description,
              name: skill.name,
              slug: skill.slug,
            });
          }}
          onNew={resetSkillDraft}
          onSave={saveSkill}
          skills={skills}
        />
      ) : null}

      {activeTab === "tasks" ? (
        <TaskManager
          canEdit={canEdit}
          draft={taskDraft}
          editingTaskPlanId={editingTaskPlanId}
          isSaving={isSaving}
          onArchive={archivePlan}
          onDelete={deletePlan}
          onDraftChange={setTaskDraft}
          onEdit={(taskPlan) => {
            setEditingTaskPlanId(taskPlan.id);
            setTaskDraft({
              acceptanceCriteria: taskPlan.acceptance_criteria.join("\n"),
              allowParallelSteps: taskPlan.allow_parallel_steps,
              objective: taskPlan.objective,
              priority: taskPlan.priority,
              status: taskPlan.status,
              steps: taskPlan.steps.map((step) => step.title).join("\n"),
            });
          }}
          onNew={resetTaskDraft}
          onSave={saveTaskPlan}
          onSetStepStatus={setStepStatus}
          taskPlans={taskPlans}
        />
      ) : null}
    </div>
  );
}

function MemoryManager({
  canEdit,
  draft,
  editingMemoryId,
  isSaving,
  memories,
  onArchive,
  onDelete,
  onDraftChange,
  onEdit,
  onNew,
  onSave,
  onSetStatus,
}: {
  canEdit: boolean;
  draft: MemoryDraft;
  editingMemoryId: string | null;
  isSaving: boolean;
  memories: AgentMemory[];
  onArchive: (memoryId: string) => Promise<void>;
  onDelete: (memoryId: string) => Promise<void>;
  onDraftChange: (draft: MemoryDraft) => void;
  onEdit: (memory: AgentMemory) => void;
  onNew: () => void;
  onSave: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  onSetStatus: (
    memoryId: string,
    memoryStatus: AgentMemoryStatus,
  ) => Promise<void>;
}) {
  const isEditing = editingMemoryId !== null;
  const canSubmit = canEdit && !isSaving && draft.text.trim() !== "";

  return (
    <div className="knowledge-grid">
      <section className="settings-panel knowledge-editor">
        <EditorHeader
          eyebrow="Memories"
          isEditing={isEditing}
          onNew={onNew}
          title={isEditing ? "Edit memory" : "New memory"}
        />
        <form className="settings-form" onSubmit={onSave}>
          <div className="two-col">
            <SelectField
              disabled={!canEdit || isSaving || isEditing}
              label="Kind"
              onChange={(value) =>
                onDraftChange({ ...draft, kind: value as AgentMemoryKind })
              }
              options={memoryKinds}
              value={draft.kind}
            />
            <SelectField
              disabled={!canEdit || isSaving || isEditing}
              label="Sensitivity"
              onChange={(value) =>
                onDraftChange({
                  ...draft,
                  sensitivity: value as AgentMemorySensitivity,
                })
              }
              options={memorySensitivities}
              value={draft.sensitivity}
            />
          </div>
          {isEditing ? (
            <SelectField
              disabled={!canEdit || isSaving}
              label="Status"
              onChange={(value) =>
                onDraftChange({
                  ...draft,
                  status: value as AgentMemoryStatus,
                })
              }
              options={memoryStatuses}
              value={draft.status}
            />
          ) : null}
          <TextAreaField
            disabled={!canEdit || isSaving}
            label="Text"
            onChange={(value) => onDraftChange({ ...draft, text: value })}
            placeholder="Prefers concise implementation updates."
            value={draft.text}
          />
          <button className="primary" disabled={!canSubmit} type="submit">
            {isEditing ? (
              <Save aria-hidden="true" size={16} />
            ) : (
              <Plus aria-hidden="true" size={16} />
            )}
            {isEditing ? "Save memory" : "Create memory"}
          </button>
        </form>
      </section>

      <section className="settings-panel knowledge-list-panel">
        <div className="section-title">
          <p className="eyebrow">Memories</p>
          <h2>Saved memories ({memories.length})</h2>
        </div>
        <div className="knowledge-list">
          {memories.length > 0 ? (
            memories.map((memory) => (
              <article className="knowledge-item" key={memory.id}>
                <ItemHeader
                  status={memory.status}
                  subtitle={`${labelFor(memory.kind)} - ${labelFor(
                    memory.sensitivity,
                  )}`}
                  title={memory.text}
                  updatedAt={memory.updated_at}
                />
                <div className="knowledge-actions">
                  <button
                    disabled={!canEdit || isSaving}
                    onClick={() => onEdit(memory)}
                    type="button"
                  >
                    <Pencil aria-hidden="true" size={15} />
                    Edit
                  </button>
                  <button
                    disabled={!canEdit || isSaving || memory.status === "active"}
                    onClick={() => void onSetStatus(memory.id, "active")}
                    type="button"
                  >
                    <CheckCircle2 aria-hidden="true" size={15} />
                    Activate
                  </button>
                  <button
                    disabled={!canEdit || isSaving || memory.status === "rejected"}
                    onClick={() => void onSetStatus(memory.id, "rejected")}
                    type="button"
                  >
                    <XCircle aria-hidden="true" size={15} />
                    Reject
                  </button>
                  <button
                    className="danger"
                    disabled={!canEdit || isSaving || memory.status === "archived"}
                    onClick={() => void onArchive(memory.id)}
                    type="button"
                  >
                    <Archive aria-hidden="true" size={15} />
                    Archive
                  </button>
                  <button
                    className="danger"
                    disabled={!canEdit || isSaving}
                    onClick={() => void onDelete(memory.id)}
                    type="button"
                  >
                    <Trash2 aria-hidden="true" size={15} />
                    Delete
                  </button>
                </div>
              </article>
            ))
          ) : (
            <p className="muted">
              No memories yet. Create one here, or let the agent propose memories
              during conversations.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}

function SkillManager({
  canEdit,
  draft,
  editingSkillSlug,
  isSaving,
  onActivate,
  onArchive,
  onDelete,
  onDraftChange,
  onEdit,
  onNew,
  onSave,
  skills,
}: {
  canEdit: boolean;
  draft: SkillDraft;
  editingSkillSlug: string | null;
  isSaving: boolean;
  onActivate: (slug: string) => Promise<void>;
  onArchive: (slug: string) => Promise<void>;
  onDelete: (slug: string) => Promise<void>;
  onDraftChange: (draft: SkillDraft) => void;
  onEdit: (skill: AgentSkill) => void;
  onNew: () => void;
  onSave: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  skills: AgentSkill[];
}) {
  const isEditing = editingSkillSlug !== null;
  const canSubmit =
    canEdit &&
    !isSaving &&
    draft.name.trim() !== "" &&
    draft.description.trim() !== "" &&
    draft.bodyMarkdown.trim() !== "";

  return (
    <div className="knowledge-grid">
      <section className="settings-panel knowledge-editor">
        <EditorHeader
          eyebrow="Skills"
          isEditing={isEditing}
          onNew={onNew}
          title={isEditing ? "Edit skill" : "New skill"}
        />
        <form className="settings-form" onSubmit={onSave}>
          <div className="two-col">
            <TextField
              disabled={!canEdit || isSaving}
              label="Name"
              onChange={(value) => onDraftChange({ ...draft, name: value })}
              placeholder="Release checklist"
              value={draft.name}
            />
            <TextField
              disabled={!canEdit || isSaving || isEditing}
              label="Slug"
              onChange={(value) => onDraftChange({ ...draft, slug: value })}
              placeholder="release-checklist"
              value={draft.slug}
            />
          </div>
          <SelectField
            disabled={!canEdit || isSaving}
            label="Category"
            onChange={(value) =>
              onDraftChange({ ...draft, category: value as AgentSkillCategory })
            }
            options={skillCategories}
            value={draft.category}
          />
          <TextField
            disabled={!canEdit || isSaving}
            label="Description"
            onChange={(value) => onDraftChange({ ...draft, description: value })}
            placeholder="Steps for release reviews."
            value={draft.description}
          />
          <TextAreaField
            disabled={!canEdit || isSaving}
            label="Body"
            onChange={(value) => onDraftChange({ ...draft, bodyMarkdown: value })}
            placeholder="Check migrations and deployment safety."
            value={draft.bodyMarkdown}
          />
          <button className="primary" disabled={!canSubmit} type="submit">
            {isEditing ? (
              <Save aria-hidden="true" size={16} />
            ) : (
              <Plus aria-hidden="true" size={16} />
            )}
            {isEditing ? "Save skill" : "Create skill"}
          </button>
        </form>
      </section>

      <section className="settings-panel knowledge-list-panel">
        <div className="section-title">
          <p className="eyebrow">Skills</p>
          <h2>Saved skills ({skills.length})</h2>
        </div>
        <div className="knowledge-list">
          {skills.length > 0 ? (
            skills.map((skill) => (
              <article className="knowledge-item" key={skill.id}>
                <ItemHeader
                  status={skill.status}
                  subtitle={`${labelFor(skill.category)} - ${skill.slug}`}
                  title={skill.name}
                  updatedAt={skill.updated_at}
                />
                <p>{skill.description}</p>
                <div className="knowledge-actions">
                  <button
                    disabled={!canEdit || isSaving}
                    onClick={() => onEdit(skill)}
                    type="button"
                  >
                    <Pencil aria-hidden="true" size={15} />
                    Edit
                  </button>
                  <button
                    disabled={!canEdit || isSaving || skill.status === "active"}
                    onClick={() => void onActivate(skill.slug)}
                    type="button"
                  >
                    <CheckCircle2 aria-hidden="true" size={15} />
                    Activate
                  </button>
                  <button
                    className="danger"
                    disabled={!canEdit || isSaving || skill.status === "archived"}
                    onClick={() => void onArchive(skill.slug)}
                    type="button"
                  >
                    <Archive aria-hidden="true" size={15} />
                    Archive
                  </button>
                  <button
                    className="danger"
                    disabled={!canEdit || isSaving}
                    onClick={() => void onDelete(skill.slug)}
                    type="button"
                  >
                    <Trash2 aria-hidden="true" size={15} />
                    Delete
                  </button>
                </div>
              </article>
            ))
          ) : (
            <p className="muted">
              No skills yet. Skills are reusable instructions the agent can follow,
              like a checklist or playbook.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}

function TaskManager({
  canEdit,
  draft,
  editingTaskPlanId,
  isSaving,
  onArchive,
  onDelete,
  onDraftChange,
  onEdit,
  onNew,
  onSave,
  onSetStepStatus,
  taskPlans,
}: {
  canEdit: boolean;
  draft: TaskDraft;
  editingTaskPlanId: string | null;
  isSaving: boolean;
  onArchive: (planId: string) => Promise<void>;
  onDelete: (planId: string) => Promise<void>;
  onDraftChange: (draft: TaskDraft) => void;
  onEdit: (taskPlan: TaskPlan) => void;
  onNew: () => void;
  onSave: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  onSetStepStatus: (stepId: string, stepStatus: TaskStatus) => Promise<void>;
  taskPlans: TaskPlan[];
}) {
  const isEditing = editingTaskPlanId !== null;
  const canSubmit =
    canEdit &&
    !isSaving &&
    draft.objective.trim() !== "" &&
    (isEditing || linesFromText(draft.steps).length > 0);

  return (
    <div className="knowledge-grid">
      <section className="settings-panel knowledge-editor">
        <EditorHeader
          eyebrow="Tasks"
          isEditing={isEditing}
          onNew={onNew}
          title={isEditing ? "Edit task plan" : "New task plan"}
        />
        <form className="settings-form" onSubmit={onSave}>
          <TextAreaField
            disabled={!canEdit || isSaving}
            label="Objective"
            onChange={(value) => onDraftChange({ ...draft, objective: value })}
            placeholder="Ship agent data management."
            value={draft.objective}
          />
          <div className="two-col">
            <SelectField
              disabled={!canEdit || isSaving}
              label="Priority"
              onChange={(value) =>
                onDraftChange({ ...draft, priority: value as TaskPriority })
              }
              options={taskPriorities}
              value={draft.priority}
            />
            <SelectField
              disabled={!canEdit || isSaving}
              label="Status"
              onChange={(value) =>
                onDraftChange({ ...draft, status: value as TaskStatus })
              }
              options={taskStatuses}
              value={draft.status}
            />
          </div>
          <TextAreaField
            disabled={!canEdit || isSaving || isEditing}
            label="Steps"
            onChange={(value) => onDraftChange({ ...draft, steps: value })}
            placeholder={"Add API\nAdd UI"}
            value={draft.steps}
          />
          <TextAreaField
            disabled={!canEdit || isSaving}
            label="Acceptance criteria"
            onChange={(value) =>
              onDraftChange({ ...draft, acceptanceCriteria: value })
            }
            placeholder="Targeted tests pass"
            value={draft.acceptanceCriteria}
          />
          <label className="check-row">
            <input
              checked={draft.allowParallelSteps}
              disabled={!canEdit || isSaving}
              onChange={(event) =>
                onDraftChange({
                  ...draft,
                  allowParallelSteps: event.currentTarget.checked,
                })
              }
              type="checkbox"
            />
            Allow parallel steps
          </label>
          <button className="primary" disabled={!canSubmit} type="submit">
            {isEditing ? (
              <Save aria-hidden="true" size={16} />
            ) : (
              <Plus aria-hidden="true" size={16} />
            )}
            {isEditing ? "Save task plan" : "Create task plan"}
          </button>
        </form>
      </section>

      <section className="settings-panel knowledge-list-panel">
        <div className="section-title">
          <p className="eyebrow">Tasks</p>
          <h2>Task plans ({taskPlans.length})</h2>
        </div>
        <div className="knowledge-list">
          {taskPlans.length > 0 ? (
            taskPlans.map((taskPlan) => (
              <article className="knowledge-item" key={taskPlan.id}>
                <ItemHeader
                  status={taskPlan.status}
                  subtitle={`${labelFor(taskPlan.priority)} priority`}
                  title={taskPlan.objective}
                  updatedAt={taskPlan.updated_at}
                />
                {taskPlan.acceptance_criteria.length > 0 ? (
                  <ul className="knowledge-criteria">
                    {taskPlan.acceptance_criteria.map((criterion) => (
                      <li key={criterion}>{criterion}</li>
                    ))}
                  </ul>
                ) : null}
                <div className="task-step-list">
                  {taskPlan.steps.map((step) => (
                    <label className="task-step-row" key={step.id}>
                      <ListChecks aria-hidden="true" size={15} />
                      <span>{step.title}</span>
                      <select
                        disabled={!canEdit || isSaving}
                        onChange={(event) =>
                          void onSetStepStatus(
                            step.id,
                            event.currentTarget.value as TaskStatus,
                          )
                        }
                        value={step.status}
                      >
                        {taskStatuses.map((statusValue) => (
                          <option key={statusValue} value={statusValue}>
                            {labelFor(statusValue)}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
                <div className="knowledge-actions">
                  <button
                    disabled={!canEdit || isSaving}
                    onClick={() => onEdit(taskPlan)}
                    type="button"
                  >
                    <Pencil aria-hidden="true" size={15} />
                    Edit
                  </button>
                  <button
                    className="danger"
                    disabled={!canEdit || isSaving || taskPlan.status === "archived"}
                    onClick={() => void onArchive(taskPlan.id)}
                    type="button"
                  >
                    <Archive aria-hidden="true" size={15} />
                    Archive
                  </button>
                  <button
                    className="danger"
                    disabled={!canEdit || isSaving}
                    onClick={() => void onDelete(taskPlan.id)}
                    type="button"
                  >
                    <Trash2 aria-hidden="true" size={15} />
                    Delete
                  </button>
                </div>
              </article>
            ))
          ) : (
            <p className="muted">
              No task plans yet. Task plans track multi-step work the agent is
              carrying out for you.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}

function EditorHeader({
  eyebrow,
  isEditing,
  onNew,
  title,
}: {
  eyebrow: string;
  isEditing: boolean;
  onNew: () => void;
  title: string;
}) {
  return (
    <div className="section-heading-row">
      <div className="section-title">
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
      </div>
      {isEditing ? (
        <button onClick={onNew} type="button">
          <Plus aria-hidden="true" size={15} />
          New
        </button>
      ) : null}
    </div>
  );
}

function SelectField({
  disabled,
  label,
  onChange,
  options,
  value,
}: {
  disabled: boolean;
  label: string;
  onChange: (value: string) => void;
  options: readonly string[];
  value: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.value)}
        value={value}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {labelFor(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

function ItemHeader({
  status,
  subtitle,
  title,
  updatedAt,
}: {
  status: string;
  subtitle: string;
  title: string;
  updatedAt: string | null;
}) {
  return (
    <div className="knowledge-item-header">
      <div>
        <h3>{title}</h3>
        <small>
          {subtitle}
          {updatedAt !== null ? ` - ${formatDate(updatedAt)}` : ""}
        </small>
      </div>
      <span className={`status-chip status-${status}`}>{labelFor(status)}</span>
    </div>
  );
}

async function runSavingAction({
  action,
  failurePrefix,
  onLog,
  setError,
  setIsSaving,
  successMessage,
}: {
  action: () => Promise<void>;
  failurePrefix: string;
  onLog: (message: string, level?: LogEntry["level"]) => void;
  setError: (message: string | null) => void;
  setIsSaving: (isSaving: boolean) => void;
  successMessage: string;
}) {
  setIsSaving(true);
  setError(null);
  try {
    await action();
    onLog(successMessage);
  } catch (error) {
    const message = errorMessage(error);
    setError(message);
    onLog(`${failurePrefix}: ${message}`, "error");
  } finally {
    setIsSaving(false);
  }
}

function upsertById<Item extends { id: string }>(items: Item[], next: Item): Item[] {
  if (items.some((item) => item.id === next.id)) {
    return items.map((item) => (item.id === next.id ? next : item));
  }
  return [next, ...items];
}

function removeById<Item extends { id: string }>(
  items: Item[],
  itemId: string,
): Item[] {
  return items.filter((item) => item.id !== itemId);
}

function upsertBySlug<Item extends { slug: string }>(
  items: Item[],
  next: Item,
): Item[] {
  if (items.some((item) => item.slug === next.slug)) {
    return items.map((item) => (item.slug === next.slug ? next : item));
  }
  return [next, ...items];
}

function removeBySlug<Item extends { slug: string }>(
  items: Item[],
  slug: string,
): Item[] {
  return items.filter((item) => item.slug !== slug);
}

function linesFromText(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

function labelFor(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
  });
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
