#!/bin/bash
#
# install-project.sh - Simplified Claude Code Project Setup
#
# This script sets up Claude Code configuration files in a project folder.
# It is a lighter version of install-claude-code.sh that only creates
# configuration files without installing prerequisites or Azure authentication.
#
# Usage: ./install-project.sh [target-directory] [marketplace-branch] [agents]
#        agents: comma-separated list of extra plugins (dwight,nafi,atlas,swa) or 'all'
#        Example: ./install-project.sh . main all
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Target directory (default to current directory)
TARGET_DIR="${1:-.}"

# Marketplace branch (default to main)
MARKETPLACE_BRANCH="${2:-main}"

# Extra agents to install (optional)
EXTRA_AGENTS="${3:-}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Claude Code Project Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "Target directory: ${GREEN}$(cd "$TARGET_DIR" && pwd)${NC}"
echo ""

# Change to target directory
cd "$TARGET_DIR"

# Step 0: Check Azure Static Web Apps CLI
echo -e "${YELLOW}Step 0: Checking Azure Static Web Apps CLI...${NC}"
if command -v swa &> /dev/null; then
    echo -e "  ${GREEN}Azure SWA CLI is installed: $(swa --version 2>/dev/null)${NC}"
else
    echo -e "  ${BLUE}Installing Azure Static Web Apps CLI...${NC}"
    npm install -g @azure/static-web-apps-cli
    if command -v swa &> /dev/null; then
        echo -e "  ${GREEN}Azure SWA CLI installed successfully${NC}"
    else
        echo -e "  ${RED}Failed to install Azure SWA CLI. Try: npm install -g @azure/static-web-apps-cli${NC}"
    fi
fi
echo ""

# Step 1: Create directory structure
echo -e "${YELLOW}Step 1: Creating directory structure...${NC}"

if [ ! -d ".claude" ]; then
    mkdir -p .claude
    echo -e "  ${GREEN}Created .claude directory${NC}"
else
    echo -e "  ${BLUE}.claude directory already exists${NC}"
fi

echo ""

# Step 2: Create configuration files
echo -e "${YELLOW}Step 2: Creating configuration files...${NC}"

# Update .claude/settings.json (merge env keys only, preserve rest)
python - << 'PYEOF'
import json, os, sys

settings_file = ".claude/settings.json"
new_env = {
    "CLAUDE_CODE_USE_FOUNDRY": "1",
    "ANTHROPIC_FOUNDRY_BASE_URL": "https://aifoundry-prod-sweden.services.ai.azure.com/anthropic",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-6",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-6"
}

status_line = {
    "type": "command",
    "command": "bash .claude/statusline.sh"
}

if os.path.exists(settings_file):
    with open(settings_file) as f:
        data = json.load(f)
    if "env" not in data:
        data["env"] = {}
    data["env"].update(new_env)
else:
    data = {"env": new_env, "enabledPlugins": {}}

data["statusLine"] = status_line

with open(settings_file, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PYEOF
echo -e "  ${GREEN}Updated .claude/settings.json (env keys + statusLine merged)${NC}"

# Copy statusline.sh to .claude/ (project-level, not plugin-specific)
STATUSLINE_SRC="$HOME/.claude/plugins/marketplaces/e61-claude-marketplace/plugins/Ralph/hooks/statusline.sh"
STATUSLINE_DST=".claude/statusline.sh"
if [ -f "$STATUSLINE_SRC" ]; then
    cp "$STATUSLINE_SRC" "$STATUSLINE_DST"
    chmod +x "$STATUSLINE_DST"
    echo -e "  ${GREEN}Copied statusline.sh to .claude/${NC}"
elif [ -f "$STATUSLINE_DST" ]; then
    echo -e "  ${BLUE}statusline.sh already exists in .claude/${NC}"
else
    echo -e "  ${YELLOW}statusline.sh not found (will be available after plugin install)${NC}"
fi

# Update .claude/settings.local.json (merge env keys only, preserve rest)
python - << 'PYEOF'
import json, os

settings_file = ".claude/settings.local.json"
new_env = {
    "APIM_SUBSCRIPTION_KEY": "d5616ca7e58d4afb841879c5a89d9c0b"
}

if os.path.exists(settings_file):
    with open(settings_file) as f:
        data = json.load(f)
    if "env" not in data:
        data["env"] = {}
    data["env"].update(new_env)
else:
    data = {"env": new_env}

with open(settings_file, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PYEOF
echo -e "  ${GREEN}Updated .claude/settings.local.json (env keys merged)${NC}"

# Update .mcp.json (ensure mcpServers key exists, preserve existing servers)
python - << 'PYEOF'
import json, os

mcp_file = ".mcp.json"

if os.path.exists(mcp_file):
    with open(mcp_file) as f:
        data = json.load(f)
    if "mcpServers" not in data:
        data["mcpServers"] = {}
else:
    data = {"mcpServers": {}}

with open(mcp_file, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PYEOF
echo -e "  ${GREEN}Updated .mcp.json (existing servers preserved)${NC}"

echo ""

# Step 3: Update .gitignore
echo -e "${YELLOW}Step 3: Updating .gitignore...${NC}"

GITIGNORE_ENTRY=".claude/settings.local.json"

if [ -f ".gitignore" ]; then
    if grep -qF "$GITIGNORE_ENTRY" .gitignore; then
        echo -e "  ${BLUE}.gitignore already contains $GITIGNORE_ENTRY${NC}"
    else
        echo "" >> .gitignore
        echo "# Claude Code local settings (contains sensitive keys)" >> .gitignore
        echo "$GITIGNORE_ENTRY" >> .gitignore
        echo -e "  ${GREEN}Added $GITIGNORE_ENTRY to .gitignore${NC}"
    fi
else
    echo "# Claude Code local settings (contains sensitive keys)" > .gitignore
    echo "$GITIGNORE_ENTRY" >> .gitignore
    echo -e "  ${GREEN}Created .gitignore with $GITIGNORE_ENTRY${NC}"
fi

echo ""

# Step 4: Install/Update Marketplace Plugins
echo -e "${YELLOW}Step 4: Installing marketplace plugins...${NC}"
echo -e "  ${BLUE}Using marketplace branch: $MARKETPLACE_BRANCH${NC}"

# Check if SSH is set up for GitHub
if ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
    echo -e "  ${GREEN}GitHub SSH authentication verified${NC}"

    # Update marketplace if exists, otherwise add it
    echo -e "  ${BLUE}Adding/updating marketplace...${NC}"
    claude plugin marketplace update e61-claude-marketplace 2>/dev/null || \
        claude plugin marketplace add "element61be/e61-claude-marketplace#$MARKETPLACE_BRANCH" || \
        echo -e "  ${YELLOW}Marketplace may already be added${NC}"

    install_plugin() {
        local name=$1
        echo -e "  ${BLUE}Installing $name plugin...${NC}"
        claude plugin install "$name@e61-claude-marketplace" -s user 2>/dev/null || \
            claude plugin update "$name@e61-claude-marketplace" 2>/dev/null || \
            echo -e "  ${YELLOW}$name plugin may already be installed${NC}"
        claude plugin enable "$name@e61-claude-marketplace" 2>/dev/null || true
        echo -e "  ${GREEN}$name plugin enabled${NC}"
    }

    install_plugin "Ralph"
    install_plugin "SkillyBilly"

    if [ -n "$EXTRA_AGENTS" ]; then
        if [ "$EXTRA_AGENTS" = "all" ]; then
            EXTRA_AGENTS="dwight,nafi,atlas,swa"
        fi
        IFS=',' read -ra AGENT_LIST <<< "$EXTRA_AGENTS"
        for agent in "${AGENT_LIST[@]}"; do
            case "$(echo "$agent" | tr '[:upper:]' '[:lower:]')" in
                dwight) install_plugin "Dwight" ;;
                nafi)   install_plugin "Nafi"   ;;
                atlas)  install_plugin "Atlas"  ;;
                swa)    install_plugin "Swa"    ;;
                *)      echo -e "  ${YELLOW}Unknown agent: $agent (skipping)${NC}" ;;
            esac
        done
    fi

    PLUGINS_INSTALLED=true
    echo -e "  ${GREEN}Marketplace plugin installation complete${NC}"
else
    PLUGINS_INSTALLED=false
    echo -e "  ${YELLOW}GitHub SSH not configured${NC}"
    echo -e "  ${YELLOW}Please run these commands manually after setting up SSH:${NC}"
    echo ""
    echo "    claude plugin marketplace add element61be/e61-claude-marketplace#$MARKETPLACE_BRANCH"
    echo "    claude plugin install Ralph@e61-claude-marketplace -s user"
    echo "    claude plugin enable Ralph@e61-claude-marketplace"
    echo "    claude plugin install SkillyBilly@e61-claude-marketplace -s user"
    echo "    claude plugin enable SkillyBilly@e61-claude-marketplace"
    if [ -n "$EXTRA_AGENTS" ]; then
        extra_resolved="$EXTRA_AGENTS"
        if [ "$extra_resolved" = "all" ]; then
            extra_resolved="dwight,nafi,atlas,swa"
        fi
        IFS=',' read -ra AGENT_LIST <<< "$extra_resolved"
        for agent in "${AGENT_LIST[@]}"; do
            case "$(echo "$agent" | tr '[:upper:]' '[:lower:]')" in
                dwight) echo "    claude plugin install Dwight@e61-claude-marketplace -s user" ; echo "    claude plugin enable Dwight@e61-claude-marketplace" ;;
                nafi)   echo "    claude plugin install Nafi@e61-claude-marketplace -s user"   ; echo "    claude plugin enable Nafi@e61-claude-marketplace" ;;
                atlas)  echo "    claude plugin install Atlas@e61-claude-marketplace -s user"  ; echo "    claude plugin enable Atlas@e61-claude-marketplace" ;;
                swa)    echo "    claude plugin install Swa@e61-claude-marketplace -s user"    ; echo "    claude plugin enable Swa@e61-claude-marketplace" ;;
            esac
        done
    fi
    echo ""
fi

echo ""

# Step 5: Verification
echo -e "${YELLOW}Step 5: Verification...${NC}"

verify_file() {
    local file=$1
    if [ -f "$file" ]; then
        echo -e "  ${GREEN}[OK]${NC} $file"
        return 0
    else
        echo -e "  ${RED}[MISSING]${NC} $file"
        return 1
    fi
}

all_ok=true
verify_file ".claude/settings.json" || all_ok=false
verify_file ".claude/settings.local.json" || all_ok=false
verify_file ".claude/statusline.sh" || all_ok=false
verify_file ".mcp.json" || all_ok=false
verify_file ".gitignore" || all_ok=false

echo ""

# Summary
echo -e "${BLUE}========================================${NC}"
if [ "$all_ok" = true ]; then
    echo -e "${GREEN}  Setup completed successfully!${NC}"
else
    echo -e "${RED}  Setup completed with errors${NC}"
fi
echo -e "${BLUE}========================================${NC}"
echo ""
echo "Files updated:"
echo "  .claude/settings.json       - Foundry env keys + statusLine merged"
echo "  .claude/settings.local.json - APIM subscription key merged (git-ignored)"
echo "  .mcp.json                   - mcpServers key ensured, existing servers preserved"
echo ""
echo "Plugins:"
if [ "$PLUGINS_INSTALLED" = true ]; then
    echo -e "  ${GREEN}Ralph@e61-claude-marketplace        - Installed and enabled${NC}"
    echo -e "  ${GREEN}SkillyBilly@e61-claude-marketplace  - Installed and enabled${NC}"
    if [ -n "$EXTRA_AGENTS" ]; then
        extra_resolved="$EXTRA_AGENTS"
        if [ "$extra_resolved" = "all" ]; then
            extra_resolved="dwight,nafi,atlas,swa"
        fi
        IFS=',' read -ra AGENT_LIST <<< "$extra_resolved"
        for agent in "${AGENT_LIST[@]}"; do
            case "$(echo "$agent" | tr '[:upper:]' '[:lower:]')" in
                dwight) echo -e "  ${GREEN}Dwight@e61-claude-marketplace       - Installed and enabled${NC}" ;;
                nafi)   echo -e "  ${GREEN}Nafi@e61-claude-marketplace         - Installed and enabled${NC}" ;;
                atlas)  echo -e "  ${GREEN}Atlas@e61-claude-marketplace        - Installed and enabled${NC}" ;;
                swa)    echo -e "  ${GREEN}Swa@e61-claude-marketplace          - Installed and enabled${NC}" ;;
            esac
        done
    fi
else
    echo -e "  ${YELLOW}Plugins not installed (GitHub SSH required)${NC}"
fi
echo ""
echo -e "${YELLOW}Next steps:${NC}"
if [ "$PLUGINS_INSTALLED" = true ]; then
    echo "  1. Run 'claude' in this directory to start Claude Code"
    echo "  2. Add MCP servers to .mcp.json as needed"
else
    echo "  1. Set up GitHub SSH access"
    echo "  2. Run the plugin installation commands shown above"
    echo "  3. Run 'claude' in this directory to start Claude Code"
    echo "  4. Add MCP servers to .mcp.json as needed"
fi
echo ""
