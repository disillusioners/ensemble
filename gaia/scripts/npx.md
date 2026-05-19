# npx (Node Package Executor)

npx is a package runner that comes bundled with npm (Node Package Manager). It allows you to run npm packages without installing them globally on your system.

---

## What is npx?

npx is part of the Node.js ecosystem and comes automatically with npm version 5.2 and above. It lets you:

- **Run packages directly** — Execute npm packages without global installation
- **Try packages easily** — Test a package without committing to an install
- **Run specific versions** — Execute a particular version of a package
- **Access built-in tools** — Run CLI tools bundled with packages

### Why npx Matters for This Project

The Context7 MCP server (a Model Context Protocol server built into this agents-ensemble project) requires npx to run `@upstreamapi/context7-mcp`. This allows AI agents to access enhanced context capabilities when exploring codebases.

---

## Installation

npx comes with Node.js, so installing Node.js automatically gives you npx.

### macOS

**Option 1: Homebrew (Recommended)**
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install node
```

**Option 2: Download Installer**
1. Visit https://nodejs.org
2. Download the LTS (Long Term Support) version for macOS
3. Run the installer package

### Linux (Debian/Ubuntu)

```bash
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt-get install -y nodejs
```

### Linux (Fedora/RHEL)

```bash
sudo dnf install nodejs
```

### Windows

**Option 1: Download Installer**
1. Visit https://nodejs.org
2. Download the LTS version for Windows
3. Run the installer

**Option 2: Winget**
```powershell
winget install OpenJS.NodeJS.LTS
```

### All Platforms: Using nvm (Node Version Manager)

nvm allows you to install and manage multiple Node.js versions:

```bash
# Install nvm (macOS/Linux)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash

# Install Node.js LTS
nvm install --lts
nvm use --lts
```

For Windows, use [nvm-windows](https://github.com/coreybutler/nvm-windows/releases).

---

## Verification

After installation, verify npx is available:

```bash
npx --version
```

**Expected output:** A version number (e.g., `10.15.0`, `9.6.0`)

You should also verify Node.js and npm:

```bash
node --version
npm --version
```

---

## Troubleshooting

### "command not found: npx"

**Cause:** Node.js is not installed, or it's not in your system PATH.

**Solutions:**

1. **Verify Node.js installation:**
   ```bash
   node --version
   ```
   If this fails, Node.js isn't installed.

2. **Check your PATH:**
   ```bash
   echo $PATH
   ```
   Make sure the Node.js bin directory is included.

3. **Reinstall Node.js** following the instructions above.

### Permission Errors

**Cause:** npm permissions issue (common on Linux/macOS).

**Solutions:**

1. **Never use sudo with npx** — This can cause permission problems.

2. **Fix npm permissions:**
   ```bash
   # Create a directory for global packages
   mkdir ~/.npm-global

   # Add to PATH in your shell config (~/.bashrc, ~/.zshrc, etc.)
   export PATH=~/.npm-global/bin:$PATH

   # Configure npm to use this directory
   npm config set prefix '~/.npm-global'
   ```

3. **For macOS with Homebrew**, use Homebrew's node which handles permissions correctly.

### Old Version Issues

**Cause:** Your npm version is older than 5.2, which means npx isn't included.

**Solutions:**

1. **Update Node.js to the latest LTS version:**
   ```bash
   # Using nvm
   nvm install --lts
   nvm use --lts

   # Using Homebrew (macOS)
   brew upgrade node

   # Using apt (Debian/Ubuntu)
   sudo apt update && sudo apt upgrade nodejs
   ```

2. **Or update npm directly:**
   ```bash
   npm install -g npm@latest
   ```

### "Cannot find package '@upstreamapi/context7-mcp'"

**Cause:** Context7 MCP package not found when running via npx.

**Solutions:**

1. **Check internet connection** — npx downloads packages on demand.

2. **Try running with explicit package:**
   ```bash
   npx @upstreamapi/context7-mcp
   ```

3. **If behind a proxy, configure npm:**
   ```bash
   npm config set proxy http://proxy.example.com:8080
   npm config set https-proxy http://proxy.example.com:8080
   ```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Check npx version | `npx --version` |
| Check node version | `node --version` |
| Check npm version | `npm --version` |
| Run a package | `npx package-name` |
| Update npm | `npm install -g npm@latest` |

---

## Next Steps

Once npx is installed and verified, you'll be able to run the Context7 MCP server for enhanced code exploration capabilities. Your development environment will be ready for AI-assisted coding tasks!
