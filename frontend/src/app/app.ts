import { Component, signal, inject, OnInit } from '@angular/core';
import { RouterOutlet, RouterLink } from '@angular/router';
import { CommonModule } from '@angular/common';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { ApiService } from './services/api.service';
import { SseService } from './services/sse.service';
import type { HealthResponse } from './models';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule,
    RouterOutlet,
    RouterLink,
    MatToolbarModule,
    MatIconModule,
    MatButtonModule
  ],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit {
  private readonly api = inject(ApiService);
  readonly sseService = inject(SseService);
  
  readonly health = signal<HealthResponse | null>(null);
  readonly isStreaming = this.sseService.isStreaming;

  ngOnInit(): void {
    this.loadHealth();
  }

  private loadHealth(): void {
    this.api.health().subscribe({
      next: (data) => {
        this.health.set(data);
      },
      error: (err) => {
        console.error('Failed to load health:', err);
      }
    });
  }
}
