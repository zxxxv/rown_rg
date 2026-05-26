import { adminHandlers } from "./handlers/admin";
import { authHandlers } from "./handlers/auth";
import { checkpointsHandlers } from "./handlers/checkpoints";
import { contradictionsHandlers } from "./handlers/contradictions";
import { libraryHandlers } from "./handlers/library";
import { presetsHandlers } from "./handlers/presets";
import { progressHandlers } from "./handlers/progress";
import { projectsHandlers } from "./handlers/projects";
import { sectionsHandlers } from "./handlers/sections";
import { sourcesHandlers } from "./handlers/sources";
import { wsHandlers } from "./handlers/ws";

export const handlers = [
  ...authHandlers,
  ...projectsHandlers,
  ...presetsHandlers,
  ...sourcesHandlers,
  ...sectionsHandlers,
  ...contradictionsHandlers,
  ...progressHandlers,
  ...checkpointsHandlers,
  ...adminHandlers,
  ...libraryHandlers,
  ...wsHandlers,
];
