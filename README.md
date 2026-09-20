# NEXUS

**Autonomous Fleet Intelligence for coordinated robot operations.**

> **People set goals. Fleets deliver.**

NEXUS is a fleet-level orchestration system for autonomous warehouse robots.  
Instead of assigning tasks to individual robots, operators specify what needs to be done and NEXUS decides:

- which robot should take the job
- how multiple robots should coordinate
- who gets right-of-way when paths conflict
- what happens if a robot fails mid-task

NEXUS is built on top of the **Bracket Bot MuJoCo simulation environment** and adds a shared warehouse intelligence layer across multiple robot simulations.

---

## Why NEXUS?

Managing one autonomous robot is a navigation problem.

Managing a fleet introduces a completely different set of challenges:

- task assignment
- robot availability
- battery and health
- cargo risk
- traffic conflicts
- queues and priorities
- failure recovery
- explainability

NEXUS treats the fleet as one coordinated workforce instead of a collection of isolated robots.

---

## Core Idea

Instead of telling a robot:

> Robot A, pick up Package 3.

The operator tells NEXUS:

> Move Package 3 to Secure Storage.

NEXUS then evaluates the fleet and decides the best way to complete the objective.

---

## Features

### Fleet Command

Operators can select one or multiple packages, choose destinations, and dispatch work to the fleet without selecting a specific robot.

NEXUS creates real jobs and distributes them across available robots.

---

### Explainable Robot Assignment

Robot selection considers factors such as:

- distance to pickup
- battery level
- robot health
- reliability
- current workload
- cargo type
- task priority

The system also exposes why a robot was selected.

For example:

> Robot A was selected because it has higher health and battery reliability for high-value cargo, even though Robot B is closer.

---

### Multiple Concurrent Jobs

Several robots can work at the same time.

If all robots are occupied, additional jobs remain queued and are automatically dispatched when fleet capacity becomes available.

---

### Context-Aware Traffic Coordination

NEXUS predicts future route conflicts before robots enter the same space.

Right-of-way can consider:

- job priority
- medical urgency
- cargo fragility
- cargo value
- whether the robot is loaded
- robot condition
- remaining distance through an intersection

A robot may yield, hold position, and automatically resume once the route becomes safe.

---

### Warehouse-Aware Navigation

Robots follow aisle-based routes instead of travelling directly through the warehouse.

The warehouse model includes:

- racks
- aisles
- intersections
- pickup access points
- destination approach points
- restricted areas
- robot stations

This makes robot movement resemble real warehouse ground operations.

---

### Shelf-Based Inventory

Packages are stored on racks rather than scattered around the warehouse floor.

Each item has:

- a visual shelf/storage position
- a valid aisle pickup access point

Robots approach inventory from the aisle and never route through shelf geometry.

---

### Robot Parking Stations

Idle robots return to the nearest available station after completing their work.

Stations can be:

- available
- reserved
- occupied

Robots release their station when assigned a new job and automatically return to an available station when idle again.

---

### Failure Recovery

NEXUS can recover from operational robot failures.

If a robot fails before pickup:

- the job returns to the queue

If a robot fails after pickup:

- the robot's last fleet position becomes the package recovery point
- the remaining task is requeued
- another robot is selected
- delivery continues automatically

Unrelated robots continue working throughout the failure.

---

## NEXUS Control Center

NEXUS includes a live browser-based operations dashboard.

The Control Center displays:

- live robot positions
- warehouse aisles and racks
- package inventory
- active routes
- robot health and battery
- current jobs
- queued jobs
- traffic conflicts
- right-of-way decisions
- yielding states
- failure recovery
- fleet statistics
- explainable assignment decisions

It also supports:

- manual Fleet Command
- multi-package dispatch
- scripted demo mode
- robot failure injection
- warehouse reset
- focus/presentation mode

---

## Architecture

```text
                       OPERATOR
                          │
                          │ Fleet objective
                          ▼
                  ┌────────────────┐
                  │     NEXUS      │
                  │ Fleet Command  │
                  └───────┬────────┘
                          │
              ┌───────────┼───────────┐
              │           │           │
              ▼           ▼           ▼
         Scheduling    Auctions    Priorities
              │           │           │
              └───────────┼───────────┘
                          ▼
                 Fleet Coordination
                          │
          ┌───────────────┼────────────────┐
          │               │                │
          ▼               ▼                ▼
      Robot A          Robot B          Robot C
      MuJoCo           MuJoCo           MuJoCo
          │               │                │
          └───────────────┼────────────────┘
                          ▼
                Shared Warehouse Model
                          │
           ┌──────────────┼──────────────┐
           ▼              ▼              ▼
        Routing        Traffic        Recovery
                          │
                          ▼
                NEXUS Control Center
