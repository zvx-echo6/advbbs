#!/bin/bash
# advBBS Setup Script
# Run this before 'docker compose up -d' on a fresh install

set -e

echo "========================================"
echo "  advBBS Setup Script"
echo "========================================"
echo ""

# Check for Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker first:"
    echo "   curl -fsSL https://get.docker.com | sh"
    echo "   sudo usermod -aG docker \$USER"
    echo "   (Log out and back in after adding to docker group)"
    exit 1
fi
echo "✓ Docker found"

# Check Docker is running
if ! docker info &> /dev/null; then
    echo "❌ Docker daemon not running. Start it with:"
    echo "   sudo systemctl start docker"
    exit 1
fi
echo "✓ Docker daemon running"

# Check for docker compose
if docker compose version &> /dev/null; then
    COMPOSE_CMD="docker compose"
    echo "✓ Docker Compose (plugin) found"
elif command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker-compose"
    echo "✓ Docker Compose (standalone) found"
else
    echo "❌ Docker Compose not found. It should be included with Docker."
    echo "   Try: sudo apt install docker-compose-plugin"
    exit 1
fi

echo ""
echo "========================================"
echo "  Platform Selection"
echo "========================================"
echo ""
echo "What platform are you installing on?"
echo ""
echo "  1) Raspberry Pi (Pi 4, Pi 5, Pi Zero 2 W, etc.)"
echo "  2) x86/x64 Linux (Desktop, Server, VM, etc.)"
echo ""

while true; do
    read -p "Enter choice [1 or 2]: " choice
    case $choice in
        1)
            PLATFORM="rpi"
            echo ""
            echo "✓ Raspberry Pi selected"
            break
            ;;
        2)
            PLATFORM="x86"
            echo ""
            echo "✓ x86/x64 Linux selected"
            break
            ;;
        *)
            echo "Please enter 1 or 2"
            ;;
    esac
done

echo ""
echo "Configuring for $PLATFORM..."

# Backup existing docker-compose.yml if it exists and isn't a symlink
if [ -f "docker-compose.yml" ] && [ ! -L "docker-compose.yml" ]; then
    cp docker-compose.yml docker-compose.yml.bak
    echo "✓ Backed up existing docker-compose.yml"
fi

# Copy the appropriate compose file
if [ "$PLATFORM" = "rpi" ]; then
    cp docker-compose.rpi.yml docker-compose.yml
    echo "✓ Configured for Raspberry Pi (ARM64)"
else
    cp docker-compose.x86.yml docker-compose.yml
    echo "✓ Configured for x86/x64 Linux"
fi

echo ""
echo "Creating data volume..."

# Create the data volume if it doesn't exist
if docker volume inspect advbbs_data &> /dev/null; then
    echo "✓ Volume 'advbbs_data' already exists"
else
    docker volume create advbbs_data
    echo "✓ Volume 'advbbs_data' created"
fi

echo ""
echo "========================================"
echo "  Setup Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo "  1. Build and start:  $COMPOSE_CMD up -d"
echo "  2. View logs:        $COMPOSE_CMD logs -f"
echo "  3. Configure:        Open http://localhost:7681"
echo ""
