# Docker Commands for Beginners

## 1. Basics
```bash
docker --version          # Check installed version
docker info               # System-wide Docker info
docker help               # List all commands
```

## 2. Images
```bash
docker pull nginx                 # Download an image
docker pull python:3.12-slim      # Pull a specific tag
docker images                     # List local images
docker rmi <image>                # Remove an image
docker image prune                # Remove unused images
docker search ubuntu              # Search Docker Hub
```

## 3. Running Containers
```bash
docker run nginx                          # Run a container
docker run -d nginx                       # Detached (background)
docker run -d -p 8080:80 nginx            # Map host port 8080 -> container 80
docker run --name myapp -d nginx          # Give container a name
docker run -it ubuntu bash                # Interactive terminal
docker run --rm ubuntu echo hello         # Auto-remove after exit
docker run -e MY_VAR=value nginx          # Set environment variable
docker run -v $(pwd):/app nginx           # Mount a volume / bind mount
```

## 4. Managing Containers
```bash
docker ps                   # Running containers
docker ps -a                # All containers (incl. stopped)
docker stop <container>     # Gracefully stop
docker start <container>    # Start a stopped container
docker restart <container>  # Restart
docker rm <container>       # Remove a container
docker rm -f <container>    # Force remove (even if running)
docker container prune      # Remove all stopped containers
```

## 5. Inspecting & Debugging
```bash
docker logs <container>             # View logs
docker logs -f <container>          # Follow logs live
docker exec -it <container> bash    # Shell into running container
docker inspect <container>          # Detailed JSON info
docker stats                        # Live CPU/memory usage
docker top <container>              # Processes in container
docker cp file.txt <container>:/app # Copy files in/out
```

## 6. Building Images
```bash
docker build -t myapp .             # Build from Dockerfile in current dir
docker build -t myapp:1.0 .         # With a tag/version
docker tag myapp user/myapp:1.0     # Tag for a registry
docker push user/myapp:1.0          # Push to Docker Hub
docker login                        # Log in to registry
```

### Sample Dockerfile
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "app.py"]
```

## 7. Volumes
```bash
docker volume create mydata         # Create a volume
docker volume ls                    # List volumes
docker volume inspect mydata        # Details
docker volume rm mydata             # Remove
docker volume prune                 # Remove unused volumes
```

## 8. Networks
```bash
docker network ls                       # List networks
docker network create mynet             # Create a network
docker run --network mynet nginx        # Attach container to network
docker network inspect mynet            # Details
docker network rm mynet                 # Remove
```

## 9. Docker Compose
```bash
docker compose up                   # Start services
docker compose up -d                # Start in background
docker compose up --build           # Rebuild images then start
docker compose down                 # Stop and remove services
docker compose ps                   # List services
docker compose logs -f              # Follow logs
```

### Sample docker-compose.yml
```yaml
services:
  web:
    build: .
    ports:
      - "8000:8000"
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: example
    volumes:
      - dbdata:/var/lib/postgresql/data
volumes:
  dbdata:
```

## 10. Cleanup
```bash
docker system df                    # Disk usage
docker system prune                 # Remove unused data
docker system prune -a --volumes    # Remove everything unused (careful!)
```

## Quick Interview Q&A
- **Image vs Container?** An image is a read-only template; a container is a running instance of it.
- **Dockerfile?** A text file with instructions to build an image.
- **CMD vs ENTRYPOINT?** CMD gives default args (overridable); ENTRYPOINT defines the fixed executable.
- **Volume vs bind mount?** Volumes are managed by Docker; bind mounts map a specific host path.
- **Docker vs VM?** Containers share the host OS kernel, so they're lighter and faster to start.
- **`EXPOSE` vs `-p`?** EXPOSE documents a port; `-p` actually publishes it to the host.
