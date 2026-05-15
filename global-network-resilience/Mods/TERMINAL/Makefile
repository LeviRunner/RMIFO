CC      = gcc
CFLAGS  = -Wall -Wextra -O2 -std=c11 \
           -D_POSIX_C_SOURCE=200809L \
           -Wno-stringop-truncation \
           -I/usr/include/postgresql
LDFLAGS = -lpq -lm

TARGET = rmifo
SRCS   = rmifo.c db.c display.c search.c routes.c telemetry.c probe.c
OBJS   = $(SRCS:.c=.o)

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(TARGET)
