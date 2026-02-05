# Pokémon Extractor & Viewer

![pokeonline](https://github.com/user-attachments/assets/e4602729-29bb-4ee4-94f6-446c90dd2a89)

This project extracts game data and graphics from the original Pokemon Red/Blue GameBoy game, puts them in a relational database, has a lightweight server to stream tile data to a renderer, and renders it in a browser. The server bit is overcomplicating a simple renderer, but I wanted it as a small proof of concept for an MMO built with this data and Phaser, which I'm working on in a different repo. 

This project uses python scripts, node.js for the server to stream tiles to a renderer, and [Phaser](https://phaser.io/) for the browser-based engine.

This project does not meaningfully distribute any copywritten material. It pulls in the disassembled code and data from the [pokered](https://github.com/pret/pokered) repo and builds database and sprites and graphics from that.


## Installation

### Cloning the Repository

```bash
# Clone with submodules (recommended)
git clone https://github.com/brynnb/pokemon-online.git --recurse-submodules

# OR clone normally and then initialize submodules
git clone https://github.com/brynnb/pokemon-online.git
cd pokemon-online
git submodule update --init --recursive
```

### Installing Dependencies

```bash
npm install
npm run export
```

## Usage

### Running the Client (Vite Development Server)

```bash
cd pokemon-phaser
npm install
npm run dev
```

This will start the Vite development server for the client on port 8080.

### Building the Client (Production)

If you want to build the client for production:

```bash
cd pokemon-phaser
npm run build
```

This will create a `dist` directory in the pokemon-phaser folder with the compiled assets.

### Running the Server (Node.js)

In a separate terminal, run the server:

```bash
# From the project root
npm run dev
```

This will start the Node.js server on port 3000.

### Running Both with a Single Command

Alternatively, you can run both the client and server with a single command:

```bash
npm run start:all
```

### Troubleshooting

If you encounter a "webfontloader" dependency error when building the client:

```bash
cd pokemon-phaser
npm install webfontloader
npm run build
```
