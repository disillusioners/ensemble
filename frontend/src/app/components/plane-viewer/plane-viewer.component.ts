import { Component, Input, OnInit } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

@Component({
  selector: 'app-plane-viewer',
  standalone: true,
  styles: [`
    :host {
      display: block;
      height: 100%;
      flex: 1;
      min-height: 0;
    }
  `],
  template: `
    @if (safeUrl) {
      <iframe [src]="safeUrl" sandbox="allow-scripts allow-same-origin allow-forms allow-popups" style="width:100%; height:100%; border:none; display:block;"></iframe>
    }
  `,
})
export class PlaneViewerComponent implements OnInit {
  @Input() url: string = '';

  safeUrl: SafeResourceUrl | null = null;

  constructor(private sanitizer: DomSanitizer) {}

  ngOnInit(): void {
    if (this.url && /^https?:\/\//i.test(this.url)) {
      this.safeUrl = this.sanitizer.bypassSecurityTrustResourceUrl(this.url);
    }
  }
}
