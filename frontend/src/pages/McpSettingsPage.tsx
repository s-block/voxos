import { McpSettingsPanel } from "../McpSettingsPanel";
import type { LogEntry } from "../appTypes";
import { useToolGrants } from "../toolGrants";

type McpSettingsPageProps = {
  canEdit: boolean;
  onLog: (message: string, level?: LogEntry["level"]) => void;
};

export function McpSettingsPage({ canEdit, onLog }: McpSettingsPageProps) {
  const { grants, setGrantsForAllAgents } = useToolGrants({ onLog });

  return (
    <div className="settings-page">
      <McpSettingsPanel
        canEdit={canEdit}
        onLog={onLog}
        onSetToolGrants={setGrantsForAllAgents}
        toolGrants={grants}
      />
    </div>
  );
}
