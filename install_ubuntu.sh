#!/usr/bin/env bash
set -euo pipefail

# Ubuntu one-line installer for Auto Stock Machine
# Usage examples:
#   bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/auto_stock_machine/main/install_ubuntu.sh) \
#     --repo-url https://github.com/YOUR_USERNAME/auto_stock_machine.git
#   bash install_ubuntu.sh --repo-url https://github.com/YOUR_USERNAME/auto_stock_machine.git

DEFAULT_REPO_URL="https://github.com/YOUR_USERNAME/auto_stock_machine.git"
REPO_URL="${DEFAULT_REPO_URL}"
INSTALL_DIR="${HOME}/auto_stock_machine"
PYTHON_BIN="python3"
BRANCH="main"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    --python-bin)
      PYTHON_BIN="${2:-}"
      shift 2
      ;;
    --branch)
      BRANCH="${2:-}"
      shift 2
      ;;
    *)
      echo "[ERROR] Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [[ -z "${REPO_URL}" || "${REPO_URL}" == "${DEFAULT_REPO_URL}" ]]; then
  echo "[ERROR] --repo-url is required."
  echo "Example: --repo-url https://github.com/your-account/auto_stock_machine.git"
  exit 1
fi

echo "[1/7] Installing Ubuntu dependencies..."
sudo apt-get update -y
sudo apt-get install -y git curl "${PYTHON_BIN}" python3-venv python3-pip

echo "[2/7] Preparing source directory..."
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  git -C "${INSTALL_DIR}" fetch --all --prune
  git -C "${INSTALL_DIR}" checkout "${BRANCH}"
  git -C "${INSTALL_DIR}" pull origin "${BRANCH}"
else
  if [[ -d "${INSTALL_DIR}" ]] && [[ -n "$(ls -A "${INSTALL_DIR}")" ]]; then
    echo "[ERROR] ${INSTALL_DIR} exists and is not a git repository."
    echo "Use --install-dir with a new path or clean it manually."
    exit 1
  fi
  git clone --branch "${BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
fi

echo "[3/7] Creating virtual environment..."
"${PYTHON_BIN}" -m venv "${INSTALL_DIR}/.venv"

echo "[4/7] Installing Python packages..."
source "${INSTALL_DIR}/.venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "${INSTALL_DIR}/requirements.txt"

echo "[5/7] Creating runtime directories..."
mkdir -p "${INSTALL_DIR}/logs" "${INSTALL_DIR}/data"

echo "[6/7] Preparing .env..."
if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
  echo "  - Created .env from .env.example"
else
  echo "  - Existing .env found, keeping current values"
fi

echo "[7/7] Done."
echo
echo "Install path : ${INSTALL_DIR}"
echo "Activate venv: source ${INSTALL_DIR}/.venv/bin/activate"
echo "Edit env file: nano ${INSTALL_DIR}/.env"
echo "Quick check  : ${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/main.py --mode status"
