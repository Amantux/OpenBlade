import type { EventLogResponse, JobResponse, RasTicketResponse } from '../types/api';
import { apiRequest } from './client';
import { getJobHistory } from './jobs';

interface EventListEnvelope {
  eventList: {
    event: EventLogResponse[];
  };
}

interface TicketListEnvelope {
  ticketList: {
    ticket: RasTicketResponse[];
  };
}

export async function getEvents(): Promise<EventLogResponse[]> {
  const response = await apiRequest<EventListEnvelope>('/events');
  return response.eventList.event;
}

export async function getRasTickets(): Promise<RasTicketResponse[]> {
  const response = await apiRequest<TicketListEnvelope>('/ras/tickets');
  return response.ticketList.ticket;
}

export function getActivity(): Promise<JobResponse[]> {
  return getJobHistory();
}
