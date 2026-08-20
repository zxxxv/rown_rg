import { adminHandlers } from "./handlers/admin";
import { authHandlers } from "./handlers/auth";
import { checkpointsHandlers } from "./handlers/checkpoints";
import { gpuHandlers } from "./handlers/gpu";
import { ipWhitelistHandlers } from "./handlers/ip-whitelist";
import { libraryHandlers } from "./handlers/library";
import { presetsHandlers } from "./handlers/presets";
import { profileHandlers } from "./handlers/profile";
import { progressHandlers } from "./handlers/progress";
import { projectsHandlers } from "./handlers/projects";
import { promptsHandlers } from "./handlers/prompts";
import { quotaSettingsHandlers } from "./handlers/quota-settings";
import { sectionsHandlers } from "./handlers/sections";
import { settingsHandlers } from "./handlers/settings";
import { sourcesHandlers } from "./handlers/sources";
import { usersHandlers } from "./handlers/users";
import { wsHandlers } from "./handlers/ws";

export const handlers = [
  ...authHandlers,
  ...projectsHandlers,
  ...presetsHandlers,
  ...sourcesHandlers,
  ...sectionsHandlers,
  ...progressHandlers,
  ...checkpointsHandlers,
  ...adminHandlers,
  ...quotaSettingsHandlers,
  ...settingsHandlers,
  ...gpuHandlers,
  ...libraryHandlers,
  ...promptsHandlers,
  // profileHandlers(users/me/*)가 usersHandlers(users/:id)보다 먼저 매칭되어야 한다
  ...profileHandlers,
  ...usersHandlers,
  ...ipWhitelistHandlers,
  ...wsHandlers,
];
