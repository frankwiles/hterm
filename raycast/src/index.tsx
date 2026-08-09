import {
  Action,
  ActionPanel,
  closeMainWindow,
  getPreferenceValues,
  Icon,
  Keyboard,
  List,
  open,
  openExtensionPreferences,
  showToast,
  Toast,
} from "@raycast/api";
import { useCallback, useEffect, useState } from "react";
import {
  checkConfig,
  configPath,
  HtermFailure,
  listProjects,
  openProject,
  type HtermErrorBody,
  type Project,
} from "./hterm";

const preferences = getPreferenceValues<Preferences.Index>();

function errorDetails(error: HtermErrorBody): string {
  const context = [error.project && `project: ${error.project}`, error.step && `step: ${error.step}`]
    .filter(Boolean)
    .join(", ");
  return context ? `${error.message} (${context})` : error.message;
}

async function failureToast(title: string, cause: unknown): Promise<void> {
  const failure = cause instanceof HtermFailure ? cause : undefined;
  const error = failure?.error ?? {
    code: "unexpected_error",
    message: cause instanceof Error ? cause.message : String(cause),
  };
  await showToast({
    style: Toast.Style.Failure,
    title: `${title} [${error.code}]`,
    message: errorDetails(error),
  });
}

function warningMessage(warnings: HtermErrorBody[]): string {
  return warnings
    .map((warning) => (typeof warning.message === "string" ? warning.message : (warning.code ?? "Unknown warning")))
    .join("; ");
}

export default function Command() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<HtermErrorBody>();

  const reload = useCallback(async () => {
    setIsLoading(true);
    setLoadError(undefined);
    try {
      setProjects(await listProjects(preferences.htermPath));
    } catch (cause) {
      const error =
        cause instanceof HtermFailure
          ? cause.error
          : { code: "unexpected_error", message: cause instanceof Error ? cause.message : String(cause) };
      setProjects([]);
      setLoadError(error);
      await failureToast("Unable to load projects", cause);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const launch = useCallback(async (project: Project, focus: boolean) => {
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: `Opening ${project.name}`,
      message: focus ? "Creating and focusing workspace" : "Creating workspace without focus",
    });
    try {
      const result = await openProject(preferences.htermPath, project.name, focus);
      if (result.warnings.length > 0) {
        toast.style = Toast.Style.Failure;
        toast.title = `Opened ${project.name} with warnings`;
        toast.message = warningMessage(result.warnings);
      } else {
        toast.style = Toast.Style.Success;
        toast.title = `Opened ${project.name}`;
        toast.message = result.workspace_id ? `Workspace ${result.workspace_id}` : undefined;
      }
      if (focus) await closeMainWindow();
    } catch (cause) {
      await toast.hide();
      await failureToast(`Unable to open ${project.name}`, cause);
    }
  }, []);

  const validate = useCallback(async () => {
    const toast = await showToast({ style: Toast.Style.Animated, title: "Validating hterm configuration" });
    try {
      const path = await checkConfig(preferences.htermPath);
      toast.style = Toast.Style.Success;
      toast.title = "hterm configuration is valid";
      toast.message = path;
    } catch (cause) {
      await toast.hide();
      await failureToast("Configuration is invalid", cause);
    }
  }, []);

  const openConfiguration = useCallback(async () => {
    try {
      const path = await configPath(preferences.htermPath);
      await open(path);
    } catch (cause) {
      await failureToast("Unable to open configuration", cause);
    }
  }, []);

  const globalActions = (
    <ActionPanel>
      <Action title="Reload Projects" icon={Icon.ArrowClockwise} onAction={reload} />
      <Action title="Validate Configuration" icon={Icon.CheckCircle} onAction={validate} />
      <Action title="Open Configuration" icon={Icon.Document} onAction={openConfiguration} />
      <Action title="Open Extension Preferences" icon={Icon.Gear} onAction={openExtensionPreferences} />
    </ActionPanel>
  );

  return (
    <List
      isLoading={isLoading}
      filtering
      navigationTitle="hterm Projects"
      searchBarPlaceholder="Search names, aliases, descriptions, keywords, and paths"
    >
      {!isLoading && projects.length === 0 ? (
        <List.EmptyView
          icon={Icon.Terminal}
          title={loadError ? "Could Not Load hterm Projects" : "No hterm Projects Configured"}
          description={
            loadError ? `[${loadError.code}] ${loadError.message}` : "Add a project to your hterm configuration"
          }
          actions={globalActions}
        />
      ) : (
        projects.map((project) => (
          <List.Item
            key={project.name}
            id={project.name}
            icon={Icon.Terminal}
            title={project.name}
            subtitle={project.description ?? project.cwd}
            keywords={[project.label, ...project.aliases, ...project.keywords, project.cwd, project.description ?? ""]}
            accessories={project.aliases.length > 0 ? [{ tag: project.aliases.join(", "), tooltip: "Aliases" }] : []}
            actions={
              <ActionPanel title={project.name}>
                <ActionPanel.Section>
                  <Action title="Open Project" icon={Icon.Terminal} onAction={() => launch(project, true)} />
                  <Action title="Open Without Focus" icon={Icon.Window} onAction={() => launch(project, false)} />
                </ActionPanel.Section>
                <ActionPanel.Section title="Configuration">
                  <Action title="Validate Configuration" icon={Icon.CheckCircle} onAction={validate} />
                  <Action title="Open Configuration" icon={Icon.Document} onAction={openConfiguration} />
                  <Action
                    title="Reload Projects"
                    icon={Icon.ArrowClockwise}
                    shortcut={Keyboard.Shortcut.Common.Refresh}
                    onAction={reload}
                  />
                </ActionPanel.Section>
                <ActionPanel.Section title="Project Path">
                  <Action.CopyToClipboard title="Copy Project Path" content={project.cwd} />
                  <Action.ShowInFinder title="Reveal Project Path" path={project.cwd} />
                </ActionPanel.Section>
                <ActionPanel.Section>
                  <Action title="Open Extension Preferences" icon={Icon.Gear} onAction={openExtensionPreferences} />
                </ActionPanel.Section>
              </ActionPanel>
            }
          />
        ))
      )}
    </List>
  );
}
