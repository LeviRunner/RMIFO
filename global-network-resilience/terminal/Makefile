CC      = gcc
CFLAGS  = -Wall -Wextra -O2 -std=c11 \
           -D_POSIX_C_SOURCE=200809L \
           -Wno-stringop-truncation \
           -I/usr/include/postgresql \
           -Isrc
LDFLAGS = -lpq -lm

TARGET  = rmifo
SRCDIR  = src
SRCS    = $(SRCDIR)/rmifo.c $(SRCDIR)/db.c $(SRCDIR)/display.c \
          $(SRCDIR)/search.c $(SRCDIR)/routes.c $(SRCDIR)/telemetry.c \
          $(SRCDIR)/probe.c
OBJS    = $(SRCS:$(SRCDIR)/%.c=%.o)

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

%.o: $(SRCDIR)/%.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(TARGET)
